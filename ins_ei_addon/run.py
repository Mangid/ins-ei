"""INS-EI Home Assistant add-on pilot runtime."""
from __future__ import annotations
import json, logging, os, time
from pathlib import Path
from ins_ei.adapters import HomeAssistantAdapter, HomeAssistantClient, mappings_from_dict
from ins_ei.collector import Collector
from ins_ei.model import Component, OperatingMode, SiteLocation, SiteModel, ThermalTopology

OPTIONS=Path("/data/options.json")

def load_options():
    with OPTIONS.open("r",encoding="utf-8") as f: return json.load(f)

def build_site(options, mappings):
    site=SiteModel(installation_id=options["installation_id"],location=SiteLocation(timezone=os.getenv("TZ","Europe/Vienna")),mode=OperatingMode.SHADOW,thermal_topology=ThermalTopology(options["thermal_topology"]))
    for component_id in sorted({m.component_id for m in mappings}):
        site.add_component(Component(id=component_id,kind=component_id.upper()))
    return site

def main():
    options=load_options(); logging.basicConfig(level=getattr(logging,options.get("log_level","INFO")),format="%(asctime)s %(levelname)s %(message)s")
    log=logging.getLogger("ins_ei"); mappings=mappings_from_dict(options)
    token=os.environ.get("SUPERVISOR_TOKEN")
    if not token: raise RuntimeError("SUPERVISOR_TOKEN missing")
    client=HomeAssistantClient("http://supervisor/core",token); site=build_site(options,mappings); collector=Collector(site,HomeAssistantAdapter(client))
    interval=int(options.get("interval_seconds",30))
    log.info("INS-EI Pilot starting | mode=SHADOW | installation=%s | mappings=%d",site.installation_id,len(mappings))
    while True:
        result=collector.collect(mappings)
        log.info("collector | read=%d good=%d stale=%d unavailable=%d unknown_component=%d",result.read,result.good,result.stale,result.unavailable,result.unknown_component)
        time.sleep(interval)

if __name__=="__main__": main()
