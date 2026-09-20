"""INS-EI Home Assistant app pilot runtime."""
from __future__ import annotations
import json, logging, os, time
from pathlib import Path

os.environ.setdefault("TZ","Europe/Vienna")
if hasattr(time,"tzset"): time.tzset()

from ins_ei.adapters import HomeAssistantAdapter, HomeAssistantClient, mappings_from_dict
from ins_ei.adapters.mapping import validate_mapping_config
from ins_ei.collector import Collector
from ins_ei.model import Component, OperatingMode, SiteLocation, SiteModel, ThermalTopology

OPTIONS=Path("/data/options.json")
UI_MAPPINGS=Path("/data/ui_mappings.json")

def load_json(path,default):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError):return default

def load_options():
    return load_json(OPTIONS,{})

def effective_config(options):
    config=dict(options)
    ui_rows=load_json(UI_MAPPINGS,None)
    if ui_rows is not None:
        config["mappings"]=ui_rows
        config["bulk_mappings"]=[]
    return config

def read_supervisor_token():
    token=os.environ.get("SUPERVISOR_TOKEN")
    if token:return token,"process_environment"
    for path in (Path("/run/s6/container_environment/SUPERVISOR_TOKEN"),Path("/var/run/s6/container_environment/SUPERVISOR_TOKEN")):
        try:
            value=path.read_text(encoding="utf-8").strip()
            if value:return value,str(path)
        except OSError:pass
    return None,"not_found"

def build_site(options,mappings):
    site=SiteModel(installation_id=options["installation_id"],location=SiteLocation(timezone=os.getenv("TZ","Europe/Vienna")),mode=OperatingMode.SHADOW,thermal_topology=ThermalTopology(options["thermal_topology"]))
    for cid in sorted({m.component_id for m in mappings}):site.add_component(Component(id=cid,kind=cid.upper()))
    return site

def main():
    options=load_options();os.environ["TZ"]=options.get("timezone","Europe/Vienna")
    if hasattr(time,"tzset"):time.tzset()
    logging.basicConfig(level=getattr(logging,options.get("log_level","INFO")),format="%(asctime)s %(levelname)s %(message)s")
    log=logging.getLogger("ins_ei")
    token,source=read_supervisor_token()
    if not token:log.error("Supervisor token unavailable; stopping safely");return
    client=HomeAssistantClient("http://supervisor/core",token)
    interval=int(options.get("interval_seconds",30))
    signature=None
    while True:
        config=effective_config(options)
        current=json.dumps(config.get("mappings",[]),sort_keys=True,ensure_ascii=False)
        if current!=signature:
            validation=validate_mapping_config(config)
            if validation.errors:
                for error in validation.errors:log.error("mapping | %s",error)
                log.error("mapping reload rejected; keeping previous valid configuration")
            else:
                mappings=mappings_from_dict(config);site=build_site(options,mappings);collector=Collector(site,HomeAssistantAdapter(client));signature=current
                log.info("mapping | active=%d source=%s",len(mappings),"web-ui" if UI_MAPPINGS.exists() else "app-options")
        if signature is not None:
            result=collector.collect(mappings)
            log.info("collector | read=%d good=%d stale=%d unavailable=%d unknown_component=%d",result.read,result.good,result.stale,result.unavailable,result.unknown_component)
        time.sleep(interval)

if __name__=="__main__":main()
