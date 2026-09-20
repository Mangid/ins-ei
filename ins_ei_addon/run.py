"""INS-EI Home Assistant app pilot runtime."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from ins_ei.adapters import HomeAssistantAdapter, HomeAssistantClient, mappings_from_dict
from ins_ei.adapters.mapping import validate_mapping_config
from ins_ei.collector import Collector
from ins_ei.discovery import discover
from ins_ei.model import Component, OperatingMode, SiteLocation, SiteModel, ThermalTopology

OPTIONS = Path("/data/options.json")


def load_options():
    with OPTIONS.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_supervisor_token():
    token = os.environ.get("SUPERVISOR_TOKEN")
    if token:
        return token, "process_environment"

    for path in (
        Path("/run/s6/container_environment/SUPERVISOR_TOKEN"),
        Path("/var/run/s6/container_environment/SUPERVISOR_TOKEN"),
    ):
        try:
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value, str(path)
        except OSError:
            pass

    return None, "not_found"


def build_site(options, mappings):
    site = SiteModel(
        installation_id=options["installation_id"],
        location=SiteLocation(timezone=os.getenv("TZ", "Europe/Vienna")),
        mode=OperatingMode.SHADOW,
        thermal_topology=ThermalTopology(options["thermal_topology"]),
    )
    for component_id in sorted({mapping.component_id for mapping in mappings}):
        site.add_component(Component(id=component_id, kind=component_id.upper()))
    return site


def main():
    options = load_options()
    logging.basicConfig(
        level=getattr(logging, options.get("log_level", "INFO")),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("ins_ei")

    validation = validate_mapping_config(options)
    if validation.errors:
        for error in validation.errors:
            log.error("mapping | %s", error)
        log.error("mapping validation failed; stopping safely")
        return

    mappings = mappings_from_dict(options)
    token, source = read_supervisor_token()
    log.info(
        "environment | supervisor_token=%s | source=%s",
        "present" if token else "MISSING",
        source,
    )
    if not token:
        log.error("Supervisor token unavailable; stopping safely")
        return

    client = HomeAssistantClient("http://supervisor/core", token)

    if options.get("discovery_on_start", True):
        states = client.states()
        candidates = discover(states, {mapping.entity_id: f"{mapping.component_id}.{mapping.point}" for mapping in mappings})
        limit = int(options.get("discovery_log_limit", 80))
        log.info(
            "discovery | entities=%d candidates=%d showing=%d",
            len(states),
            len(candidates),
            min(limit, len(candidates)),
        )
        for candidate in candidates[:limit]:
            log.info(
                "discovery | status=%s domain=%s point=%s score=%d entity=%s state=%s unit=%s device_class=%s name=%s",
                f"MAPPED:{candidate.mapped_to}" if candidate.mapped_to else "CANDIDATE",
                candidate.suggested_domain,
                candidate.suggested_point,
                candidate.score,
                candidate.entity_id,
                candidate.state,
                candidate.unit,
                candidate.device_class,
                candidate.name,
            )

    site = build_site(options, mappings)
    collector = Collector(site, HomeAssistantAdapter(client))
    interval = int(options.get("interval_seconds", 30))

    log.info(
        "INS-EI Pilot starting | mode=SHADOW | installation=%s | mappings=%d",
        site.installation_id,
        len(mappings),
    )

    while True:
        result = collector.collect(mappings)
        log.info(
            "collector | read=%d good=%d stale=%d unavailable=%d unknown_component=%d",
            result.read,
            result.good,
            result.stale,
            result.unavailable,
            result.unknown_component,
        )
        for component in site.components.values():
            for point_name, point in component.points.items():
                log.info(
                    "point | %s.%s=%s %s | quality=%s | source=%s | timestamp=%s",
                    component.id,
                    point_name,
                    point.value,
                    point.unit or "",
                    point.quality.value,
                    point.source,
                    point.timestamp.isoformat() if point.timestamp else "none",
                )
        time.sleep(interval)


if __name__ == "__main__":
    main()
