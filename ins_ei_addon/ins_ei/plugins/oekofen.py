"""OekoFEN plugin profiles.

The plugin deliberately separates raw controller keys from canonical INS-EI
points. Profiles can be extended for additional controller/model generations
without changing the optimizer.
"""
from __future__ import annotations

PROFILES = {
    "generic": {
        "aliases": {},
        "capabilities": set(),
    },
}

CANONICAL_TARGETS = {
    "boiler_temperature": ("pellet_boiler", "boiler_temperature"),
    "flow_temperature": ("pellet_boiler", "flow_temperature"),
    "return_temperature": ("pellet_boiler", "return_temperature"),
    "runtime": ("pellet_boiler", "runtime"),
    "burner_starts": ("pellet_boiler", "burner_starts"),
    "fuel_consumption_total": ("pellet_boiler", "fuel_consumption_total"),
    "buffer_temperature_upper": ("buffer", "temperature_upper"),
    "buffer_temperature_lower": ("buffer", "temperature_lower"),
    "dhw_temperature": ("dhw", "temperature"),
}

def normalize(raw: dict, profile: str = "generic") -> tuple[list[dict], dict]:
    cfg = PROFILES.get(profile, PROFILES["generic"])
    aliases = cfg.get("aliases", {})
    points, unmapped = [], {}
    for raw_key, value in raw.items():
        semantic = aliases.get(raw_key, raw_key)
        target = CANONICAL_TARGETS.get(semantic)
        if target is None:
            unmapped[raw_key] = value
            continue
        points.append({"component_id": target[0], "point": target[1], "value": value, "source": raw_key})
    return points, unmapped
