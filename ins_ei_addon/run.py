"""INS-EI runtime: persistent installation model + collector."""
from __future__ import annotations
import json,logging,os,time
from pathlib import Path
from ins_ei.adapters import HomeAssistantAdapter,HomeAssistantClient,mappings_from_dict
from ins_ei.adapters.mapping import validate_mapping_config
from ins_ei.collector import Collector
from ins_ei.discovery import discover
from ins_ei.model import Component,OperatingMode,SiteLocation,SiteModel,ThermalTopology
from ins_ei.shadow import evaluate as shadow_evaluate

OPTIONS=Path("/data/options.json");UI=Path("/data/ui_mappings.json");DISC=Path("/data/discovery.json");COMPONENTS=Path("/data/components.json");SITE=Path("/data/site_model.json");SHADOW=Path("/data/shadow_decision.json");MARKET=Path("/data/market.json");MARKET_SERIES=Path("/data/market_series.json")
MULTI={"HEATING_CIRCUIT","ROOM","LOAD"}

def load(path,default):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError):return default

def supervisor_token():
    value=os.environ.get("SUPERVISOR_TOKEN")
    if value:return value
    for path in (Path("/run/s6/container_environment/SUPERVISOR_TOKEN"),Path("/var/run/s6/container_environment/SUPERVISOR_TOKEN")):
        try:
            value=path.read_text().strip()
            if value:return value
        except OSError:pass
    return None

def base_kind(component_id):
    raw=component_id.upper()
    for kind in ("COMBINED_STORAGE","HEATING_CIRCUIT","POWER_TO_HEAT","PELLET_BOILER","HEAT_PUMP","FORECAST","MARKET","BATTERY","BUFFER","DHW","GRID","LOAD","PV","ROOM"):
        if raw==kind or raw.startswith(kind+"_") or raw.startswith(kind+":"):return kind
    return raw

def effective_config(options):
    result=dict(options);rows=load(UI,None)
    if rows is not None:result["mappings"]=rows
    return result

def configured_components(component_cfg,mappings):
    result=[]
    mapped_ids={m.component_id for m in mappings}
    for kind,data in component_cfg.items():
        if kind in MULTI:
            for inst in data.get("instances",[]):
                result.append((inst["id"],kind,True,{"name":inst.get("name",inst["id"])}))
        elif data.get("enabled"):
            result.append((kind.lower(),kind,True,{}))
    known={x[0] for x in result}
    configured_singletons={kind for _,kind,_,_ in result if kind not in MULTI}
    existing_kinds={kind for _,kind,_,_ in result}
    for cid in mapped_ids:
        kind=base_kind(cid)
        if cid in known:
            continue
        if kind not in MULTI and kind in existing_kinds:
            continue
        result.append((cid,kind,True,{"legacy_mapping":True}))
        known.add(cid)
        existing_kinds.add(kind)
    return result

def build_site(options,component_cfg,mappings,market_cfg):
    model=SiteModel(
        installation_id=options["installation_id"],
        location=SiteLocation(timezone=options.get("timezone","Europe/Vienna")),
        mode=OperatingMode.SHADOW,
        thermal_topology=ThermalTopology(options["thermal_topology"]),
    )
    for cid,kind,enabled,metadata in configured_components(component_cfg,mappings):
        model.add_component(Component(id=cid,kind=kind,name=metadata.get("name"),enabled=enabled,config=metadata))
    # Canonicalize singleton mapping IDs to the actual SiteModel component IDs.
    # UI mappings use catalog names such as FORECAST/MARKET while configured
    # singleton components may be stored lowercase.
    singleton_ids={}
    for component in model.components.values():
        if component.kind not in MULTI:singleton_ids.setdefault(component.kind,component.id)
    mappings=[
        mapping if base_kind(mapping.component_id) not in singleton_ids else
        mapping.__class__(
            component_id=singleton_ids[base_kind(mapping.component_id)],
            point=mapping.point,
            entity_id=mapping.entity_id,
            unit=mapping.unit,
            role=mapping.role,
            scale=mapping.scale,
            invert_sign=mapping.invert_sign,
            freshness=mapping.freshness,
            max_age_seconds=mapping.max_age_seconds,
        )
        for mapping in mappings
    ]
    market_components=model.components_by_kind("MARKET")
    if market_components:
        market_components[0].config=market_cfg
    else:
        model.add_component(Component(id="market",kind="MARKET",enabled=True,config=market_cfg))
    return model,mappings

def persist_site(model):
    payload={"installation_id":model.installation_id,"mode":model.mode.value,"thermal_topology":model.thermal_topology.value if model.thermal_topology else None,"components":[{"id":c.id,"kind":c.kind,"enabled":c.enabled,"name":c.name,"config":c.config} for c in model.components.values()]}
    SITE.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")

def snapshot(client,mappings):
    mapped={m.entity_id:f"{m.component_id}.{m.point}" for m in mappings};items=discover(client.states(),mapped)
    DISC.write_text(json.dumps([{"entity_id":x.entity_id,"name":x.name,"state":x.state,"unit":x.unit,"suggested_domain":x.suggested_domain,"suggested_point":x.suggested_point,"score":x.score,"mapped_to":x.mapped_to,"source_kind":x.source_kind} for x in items],ensure_ascii=False,indent=2),encoding="utf-8")

def main():
    options=load(OPTIONS,{});os.environ["TZ"]=options.get("timezone","Europe/Vienna")
    if hasattr(time,"tzset"):time.tzset()
    logging.basicConfig(level=getattr(logging,options.get("log_level","INFO")),format="%(asctime)s %(levelname)s %(message)s");log=logging.getLogger("ins_ei")
    token=supervisor_token()
    if not token:log.error("Supervisor token unavailable");return
    client=HomeAssistantClient("http://supervisor/core",token);signature=None;last=0;interval=int(options.get("interval_seconds",30))
    while True:
        cfg=effective_config(options);component_cfg=load(COMPONENTS,{});market_cfg=load(MARKET,{"mode":"AWATTAR_AT","import_markup_ct":1.5,"vat_percent":20.0,"export_factor_percent":81.0})
        sig=json.dumps({"mappings":cfg.get("mappings",[]),"components":component_cfg,"market":market_cfg,"topology":options.get("thermal_topology")},sort_keys=True,ensure_ascii=False)
        if sig!=signature:
            check=validate_mapping_config(cfg)
            if check.errors:
                for error in check.errors:log.error("mapping | %s",error)
            else:
                mappings=mappings_from_dict(cfg);model,mappings=build_site(options,component_cfg,mappings,market_cfg);errors=model.validate()
                if errors:
                    for error in errors:log.error("site model | %s",error)
                else:
                    persist_site(model);collector=Collector(model,HomeAssistantAdapter(client));signature=sig
                    kinds={}
                    for component in model.components.values():kinds[component.kind]=kinds.get(component.kind,0)+1
                    log.info("site model | components=%d kinds=%s topology=%s ids=%s",len(model.components),kinds,model.thermal_topology.value if model.thermal_topology else "none",sorted(model.components))
                    log.info("mapping | active=%d",len(mappings));snapshot(client,mappings)
                    for mapping in mappings:
                        if base_kind(mapping.component_id)=="MARKET" and mapping.point=="spot_price":
                            state=client.state(mapping.entity_id);attrs=state.get("attributes") or {};series=attrs.get("data") or []
                            MARKET_SERIES.write_text(json.dumps(series,ensure_ascii=False,indent=2),encoding="utf-8")
                            log.info("market series | entity=%s | slots=%d",mapping.entity_id,len(series))
                    last=time.time()
        if signature is not None:
            result=collector.collect(mappings)
            log.info("collector | read=%d good=%d stale=%d unavailable=%d",result.read,result.good,result.stale,result.unavailable)
            if result.stale or result.unavailable:
                for component in model.components.values():
                    for point_name,point in component.points.items():
                        if point.quality.value in ("STALE","UNAVAILABLE"):
                            log.info("collector issue | %s.%s | quality=%s | value=%s %s | source=%s",component.id,point_name,point.quality.value,point.value,point.unit or "",point.source)
            decision=shadow_evaluate(model,load(MARKET_SERIES,[]))
            SHADOW.write_text(json.dumps(decision.to_dict(),ensure_ascii=False,indent=2),encoding="utf-8")
            spot_input=decision.inputs.get("market_spot_price",{})
            log.info("market price | spot=%s %s | import=%.3f ct/kWh | export=%.3f ct/kWh",spot_input.get("value"),spot_input.get("unit") or "",decision.inputs.get("import_price_ct_kwh") or 0,decision.inputs.get("export_price_ct_kwh") or 0)
            log.info("forecast | pv_today=%s kWh [%s/%s] | consumption_today=%s kWh [%s/%s] | pv_hour=%s kWh [%s/%s] | consumption_hour=%s kWh [%s/%s] | balance=%s kWh",
                decision.inputs.get("forecast_pv_today",{}).get("value"),decision.inputs.get("forecast_pv_today",{}).get("quality"),decision.inputs.get("forecast_pv_today",{}).get("source"),
                decision.inputs.get("forecast_consumption_today",{}).get("value"),decision.inputs.get("forecast_consumption_today",{}).get("quality"),decision.inputs.get("forecast_consumption_today",{}).get("source"),
                decision.inputs.get("forecast_pv_current_hour",{}).get("value"),decision.inputs.get("forecast_pv_current_hour",{}).get("quality"),decision.inputs.get("forecast_pv_current_hour",{}).get("source"),
                decision.inputs.get("forecast_consumption_current_hour",{}).get("value"),decision.inputs.get("forecast_consumption_current_hour",{}).get("quality"),decision.inputs.get("forecast_consumption_current_hour",{}).get("source"),
                decision.inputs.get("forecast_balance_today_kwh"))
            log.info("price context | current=%.3f ct/kWh | next_slots=%s | min=%s | avg=%s | max=%s | class=%s",
                decision.inputs.get("market_current_spot_ct") or 0,
                decision.inputs.get("market_future_slots"),
                round(decision.inputs.get("market_future_spot_min_ct"),3) if decision.inputs.get("market_future_spot_min_ct") is not None else None,
                round(decision.inputs.get("market_future_spot_avg_ct"),3) if decision.inputs.get("market_future_spot_avg_ct") is not None else None,
                round(decision.inputs.get("market_future_spot_max_ct"),3) if decision.inputs.get("market_future_spot_max_ct") is not None else None,
                decision.inputs.get("market_price_class"))
            log.info("shadow | action=%s | confidence=%s | reason=%s",decision.action,decision.confidence,decision.reason)
            if time.time()-last>300:snapshot(client,mappings);last=time.time()
        time.sleep(interval)

if __name__=="__main__":main()
