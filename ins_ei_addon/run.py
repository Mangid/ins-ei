"""INS-EI runtime with collector and discovery snapshot."""
from __future__ import annotations
import json, logging, os, time
from pathlib import Path
from ins_ei.adapters import HomeAssistantAdapter, HomeAssistantClient, mappings_from_dict
from ins_ei.adapters.mapping import validate_mapping_config
from ins_ei.collector import Collector
from ins_ei.discovery import discover
from ins_ei.model import Component, OperatingMode, SiteLocation, SiteModel, ThermalTopology
OPTIONS=Path("/data/options.json"); UI=Path("/data/ui_mappings.json"); DISC=Path("/data/discovery.json")
def load(path,default):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError):return default
def token():
    value=os.environ.get("SUPERVISOR_TOKEN")
    if value:return value
    for p in (Path("/run/s6/container_environment/SUPERVISOR_TOKEN"),Path("/var/run/s6/container_environment/SUPERVISOR_TOKEN")):
        try:
            value=p.read_text().strip()
            if value:return value
        except OSError:pass
def config(options):
    result=dict(options);rows=load(UI,None)
    if rows is not None:result["mappings"]=rows
    return result
def site(options,mappings):
    s=SiteModel(options["installation_id"],SiteLocation(timezone=options.get("timezone","Europe/Vienna")),OperatingMode.SHADOW,ThermalTopology(options["thermal_topology"]))
    for cid in sorted({m.component_id for m in mappings}):s.add_component(Component(id=cid,kind=cid.upper()))
    return s
def snapshot(client,mappings):
    mapped={m.entity_id:f"{m.component_id}.{m.point}" for m in mappings};items=discover(client.states(),mapped)
    DISC.write_text(json.dumps([{"entity_id":x.entity_id,"name":x.name,"state":x.state,"unit":x.unit,"suggested_domain":x.suggested_domain,"suggested_point":x.suggested_point,"score":x.score,"mapped_to":x.mapped_to,"source_kind":x.source_kind} for x in items],ensure_ascii=False,indent=2))
def main():
    options=load(OPTIONS,{});os.environ["TZ"]=options.get("timezone","Europe/Vienna")
    if hasattr(time,"tzset"):time.tzset()
    logging.basicConfig(level=getattr(logging,options.get("log_level","INFO")),format="%(asctime)s %(levelname)s %(message)s");log=logging.getLogger("ins_ei")
    t=token()
    if not t:log.error("Supervisor token unavailable");return
    client=HomeAssistantClient("http://supervisor/core",t);signature=None;last=0;interval=int(options.get("interval_seconds",30))
    while True:
        cfg=config(options);sig=json.dumps(cfg.get("mappings",[]),sort_keys=True)
        if sig!=signature:
            check=validate_mapping_config(cfg)
            if check.errors:
                for e in check.errors:log.error("mapping | %s",e)
            else:
                mappings=mappings_from_dict(cfg);model=site(options,mappings);collector=Collector(model,HomeAssistantAdapter(client));signature=sig;log.info("mapping | active=%d",len(mappings));snapshot(client,mappings);last=time.time()
        if signature is not None:
            r=collector.collect(mappings);log.info("collector | read=%d good=%d stale=%d unavailable=%d",r.read,r.good,r.stale,r.unavailable)
            if time.time()-last>300:snapshot(client,mappings);last=time.time()
        time.sleep(interval)
if __name__=="__main__":main()
