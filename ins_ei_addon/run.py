"""INS-EI runtime: persistent installation model + collector."""
from __future__ import annotations
import json,logging,os,time
from urllib.request import Request,urlopen
from urllib.error import URLError,HTTPError
from pathlib import Path
from ins_ei.adapters import HomeAssistantAdapter,HomeAssistantClient,mappings_from_dict
from ins_ei.adapters.mapping import validate_mapping_config
from ins_ei.collector import Collector
from ins_ei.discovery import discover
from ins_ei.model import Component,OperatingMode,SiteLocation,SiteModel,ThermalTopology
from ins_ei.shadow import evaluate as shadow_evaluate
from ins_ei.plugins.oekofen import OekoFENPlugin
from ins_ei.plugins.mypv import MyPVPlugin
from ins_ei.plugins.shrdzm import SHRDZMPlugin
from ins_ei.model import DataPoint,DataQuality,DataRole

OPTIONS=Path("/data/options.json");UI=Path("/data/ui_mappings.json");VRM_SERIES=Path("/data/vrm_forecast_series.json");DISC=Path("/data/discovery.json");COMPONENTS=Path("/data/components.json");SITE=Path("/data/site_model.json");SHADOW=Path("/data/shadow_decision.json");MARKET=Path("/data/market.json");MARKET_SERIES=Path("/data/market_series.json");STRATEGY=Path("/data/strategy.json");SERVER=Path("/data/server.json");TELEMETRY_STATUS=Path("/data/telemetry_status.json");PLUGINS=Path("/data/plugins.json")
MULTI={"HEATING_CIRCUIT","ROOM","LOAD"}

def load(path,default):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError):return default

def send_telemetry(url,installation_id,model,decision,timeout=5):
    if not url:return None
    data={}
    for component in model.components.values():
        for name,point in component.points.items():
            if point.value is not None and point.quality.value=="GOOD":
                data[f"{component.id}.{name}"]=point.value
    data["shadow.action"]=decision.action
    data["shadow.confidence"]=decision.confidence
    payload={"installation_id":installation_id,"timestamp":decision.timestamp,"data":data}
    req=Request(url.rstrip("/")+"/api/v1/telemetry",data=json.dumps(payload,ensure_ascii=False).encode("utf-8"),headers={"Content-Type":"application/json"},method="POST")
    try:
        with urlopen(req,timeout=timeout) as response:return response.status,len(data)
    except (URLError,HTTPError,TimeoutError,OSError) as exc:return exc,None

_PLUGIN_CACHE={};_PLUGIN_SIGNATURES={}

def apply_plugin_probe(model,probe):
    for item in probe.points:
        component=model.component(item.component_id)
        if component is None:
            kind=base_kind(item.component_id)
            component=Component(id=item.component_id,kind=kind,enabled=True,config={"plugin":probe.identity.manufacturer,"model":probe.identity.model})
            model.add_component(component)
        component.points[item.point]=DataPoint(key=f"{item.component_id}.{item.point}",value=item.value,unit=item.unit,quality=DataQuality.GOOD,source=item.source,role=DataRole.MONITORING)
    return len(probe.points)

def read_plugins(model,config,log):
    count=0
    o=config.get("oekofen") or {}
    if o.get("enabled") and o.get("host") and o.get("password"):
        try:
            plugin=_PLUGIN_CACHE.get("oekofen")
            signature=(o["host"],o["password"],o.get("port",4321))
            if plugin is None or _PLUGIN_SIGNATURES.get("oekofen")!=signature:
                plugin=OekoFENPlugin(*signature);_PLUGIN_CACHE["oekofen"]=plugin;_PLUGIN_SIGNATURES["oekofen"]=signature
            probe=plugin.read()
            count+=apply_plugin_probe(model,probe)
            log.info("plugin | oekofen | model=%s profile=%s points=%d unmapped=%d",probe.identity.model,probe.identity.profile,len(probe.points),len(probe.unmapped))
        except Exception as exc:log.warning("plugin | oekofen failed | %s",exc)
    m=config.get("mypv") or {}
    if m.get("enabled") and m.get("host"):
        try:
            signature=(m["host"],m.get("port",502),m.get("unit_id",1),m.get("http_enabled",True))
            plugin=_PLUGIN_CACHE.get("mypv")
            if plugin is None or _PLUGIN_SIGNATURES.get("mypv")!=signature:
                plugin=MyPVPlugin(*signature);_PLUGIN_CACHE["mypv"]=plugin;_PLUGIN_SIGNATURES["mypv"]=signature
            probe=plugin.read()
            count+=apply_plugin_probe(model,probe)
            log.info("plugin | mypv | model=%s profile=%s points=%d unmapped=%d",probe.identity.model,probe.identity.profile,len(probe.points),len(probe.unmapped))
        except Exception as exc:log.warning("plugin | mypv failed | %s",exc)
    z=config.get("shrdzm") or {}
    if z.get("enabled") and z.get("host"):
        try:
            signature=(z["host"],z.get("port",502),z.get("unit_id",1),json.dumps(z.get("registers",{}),sort_keys=True))
            plugin=_PLUGIN_CACHE.get("shrdzm")
            if plugin is None or _PLUGIN_SIGNATURES.get("shrdzm")!=signature:
                plugin=SHRDZMPlugin(z["host"],z.get("port",502),z.get("unit_id",1),z.get("registers",{}));_PLUGIN_CACHE["shrdzm"]=plugin;_PLUGIN_SIGNATURES["shrdzm"]=signature
            probe=plugin.read();count+=apply_plugin_probe(model,probe)
            log.info("plugin | shrdzm | model=%s profile=%s points=%d diagnostics=%d",probe.identity.family,probe.identity.profile,len(probe.points),len(probe.unmapped))
        except Exception as exc:log.warning("plugin | shrdzm failed | %s",exc)
    return count

def read_vrm_forecast(config,log):
    v=config.get("vrm") or {}
    if not v.get("enabled") or not v.get("installation_id") or not v.get("authorization"):return None
    now=int(time.time());end=now+48*3600
    url=f"https://vrmapi.victronenergy.com/v2/installations/{v['installation_id']}/stats?type=forecast&interval=hours&start={now}&end={end}"
    req=Request(url,headers={"X-Authorization":v["authorization"],"Accept":"application/json"})
    try:
        with urlopen(req,timeout=30) as response:data=json.loads(response.read().decode("utf-8"))
        records=data.get("records") or {};pv=records.get("solar_yield_forecast") or [];load_fc=records.get("vrm_consumption_fc") or []
        pvd={int(x[0]):float(x[1])/1000.0 for x in pv if len(x)>=2};ld={int(x[0]):float(x[1])/1000.0 for x in load_fc if len(x)>=2}
        slots=[{"timestamp_ms":ts,"pv_kwh":pvd[ts],"load_kwh":ld[ts]} for ts in sorted(set(pvd)&set(ld))]
        VRM_SERIES.write_text(json.dumps(slots,ensure_ascii=False,indent=2),encoding="utf-8")
        log.info("vrm forecast series | pv_slots=%d | load_slots=%d | common_slots=%d | pv_kwh=%.3f | load_kwh=%.3f",len(pv),len(load_fc),len(slots),sum(x["pv_kwh"] for x in slots),sum(x["load_kwh"] for x in slots))
        return slots
    except Exception as exc:
        log.warning("vrm forecast series failed | %s",exc);return None

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
    # Manufacturer plugins use short instance IDs for heating circuits.
    # Normalize them to the vendor-neutral SiteModel kind so Thermal Shadow
    # sees their target flow temperatures and pump states.
    if raw.startswith("HK") and raw[2:].isdigit():
        return "HEATING_CIRCUIT"
    for kind in ("COMBINED_STORAGE","HEATING_CIRCUIT","POWER_TO_HEAT","PELLET_BOILER","HEAT_PUMP","SOLAR_THERMAL","FORECAST","MARKET","BATTERY","BUFFER","DHW","GRID","LOAD","PV","ROOM"):
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
            result.append((kind.lower(),kind,True,dict(data.get("config") or {})))
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
    client=HomeAssistantClient("http://supervisor/core",token);signature=None;last=0;interval=int(options.get("interval_seconds",30));server_cfg=load(SERVER,{"url":"https://ins-ei.ins-enertech.net","installation_id":options.get("installation_id","pilot-local"),"interval_seconds":30,"enabled":False});telemetry_url=server_cfg.get("url","").strip() if server_cfg.get("enabled") else "";telemetry_interval=int(server_cfg.get("interval_seconds",30));last_telemetry=0
    while True:
        cfg=effective_config(options);component_cfg=load(COMPONENTS,{});market_cfg=load(MARKET,{"mode":"AWATTAR_AT","import_markup_ct":1.5,"vat_percent":20.0,"export_factor_percent":81.0});strategy_cfg=load(STRATEGY,{"profile":"AUTO","priorities":{"thermal_storage":80,"battery_economics":60,"export":40,"ev":50},"requirements":{"dhw_min_c":50.0}})
        sig=json.dumps({"mappings":cfg.get("mappings",[]),"components":component_cfg,"market":market_cfg,"strategy":strategy_cfg,"topology":options.get("thermal_topology")},sort_keys=True,ensure_ascii=False)
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
            plugin_cfg=load(PLUGINS,{})
            plugin_points=read_plugins(model,plugin_cfg,log)
            if not VRM_SERIES.exists() or time.time()-VRM_SERIES.stat().st_mtime>=900:
                read_vrm_forecast(plugin_cfg,log)
            log.info("collector | read=%d good=%d stale=%d unavailable=%d plugin_points=%d",result.read,result.good,result.stale,result.unavailable,plugin_points)
            if result.stale or result.unavailable:
                for component in model.components.values():
                    for point_name,point in component.points.items():
                        if point.quality.value in ("STALE","UNAVAILABLE"):
                            log.info("collector issue | %s.%s | quality=%s | value=%s %s | source=%s",component.id,point_name,point.quality.value,point.value,point.unit or "",point.source)
            decision=shadow_evaluate(model,load(MARKET_SERIES,[]),strategy_cfg)
            SHADOW.write_text(json.dumps(decision.to_dict(),ensure_ascii=False,indent=2),encoding="utf-8")
            spot_input=decision.inputs.get("market_spot_price",{})
            log.info("market price | spot=%s %s | import=%.3f ct/kWh | export=%.3f ct/kWh",spot_input.get("value"),spot_input.get("unit") or "",decision.inputs.get("import_price_ct_kwh") or 0,decision.inputs.get("export_price_ct_kwh") or 0)
            log.info("forecast | pv_today=%s kWh [%s/%s] | consumption_today=%s kWh [%s/%s] | pv_hour=%s kWh [%s/%s] | consumption_hour=%s kWh [%s/%s] | balance=%s kWh",
                decision.inputs.get("forecast_pv_today",{}).get("value"),decision.inputs.get("forecast_pv_today",{}).get("quality"),decision.inputs.get("forecast_pv_today",{}).get("source"),
                decision.inputs.get("forecast_consumption_today",{}).get("value"),decision.inputs.get("forecast_consumption_today",{}).get("quality"),decision.inputs.get("forecast_consumption_today",{}).get("source"),
                decision.inputs.get("forecast_pv_current_hour",{}).get("value"),decision.inputs.get("forecast_pv_current_hour",{}).get("quality"),decision.inputs.get("forecast_pv_current_hour",{}).get("source"),
                decision.inputs.get("forecast_consumption_current_hour",{}).get("value"),decision.inputs.get("forecast_consumption_current_hour",{}).get("quality"),decision.inputs.get("forecast_consumption_current_hour",{}).get("source"),
                decision.inputs.get("forecast_balance_today_kwh"))
            bt=decision.inputs.get("thermal_buffer") or {};dt=decision.inputs.get("thermal_dhw") or {}
            log.info("thermal | buffer_available=%s kWh | buffer_free=%s kWh | buffer_mean=%s C | buffer_temps=%s | buffer_min_off=%s C | buffer_quality=%s | dhw_available=%s kWh | dhw_free=%s kWh | dhw_mean=%s C | dhw_temps=%s | dhw_quality=%s | pellet_heat=%s ct/kWh",
                round(bt.get("available_kwh"),2) if bt.get("available_kwh") is not None else None,
                round(bt.get("free_kwh"),2) if bt.get("free_kwh") is not None else None,
                round(bt.get("estimated_mean_c"),1) if bt.get("estimated_mean_c") is not None else None,bt.get("temperatures"),
                decision.inputs.get("buffer_min_temperature_off",{}).get("value"),bt.get("quality"),
                round(dt.get("available_kwh"),2) if dt.get("available_kwh") is not None else None,
                round(dt.get("free_kwh"),2) if dt.get("free_kwh") is not None else None,
                round(dt.get("estimated_mean_c"),1) if dt.get("estimated_mean_c") is not None else None,dt.get("temperatures"),dt.get("quality"),
                round(decision.inputs.get("pellet_heat_cost_ct_kwh"),2) if decision.inputs.get("pellet_heat_cost_ct_kwh") is not None else None)
            be=decision.inputs.get("battery_energy") or {}
            log.info("battery energy | capacity=%s kWh | available=%s kWh | free=%s kWh | min_soc=%s %% | max_soc=%s %% | pv_surplus_after_battery=%s kWh",
                be.get("capacity_kwh"),round(be.get("available_kwh"),2) if be.get("available_kwh") is not None else None,
                round(be.get("free_kwh"),2) if be.get("free_kwh") is not None else None,be.get("min_soc_percent"),be.get("max_soc_percent"),
                round(decision.inputs.get("pv_surplus_after_battery_headroom_kwh"),2) if decision.inputs.get("pv_surplus_after_battery_headroom_kwh") is not None else None)
            te=decision.inputs.get("thermal_economics") or {}
            log.info("thermal economics | decision=%s | surplus_after_battery=%s kWh | export=%s ct/kWh | pellet_heat=%s ct/kWh | advantage_heat=%s ct/kWh | reason=%s",
                te.get("decision"),round(te.get("surplus_after_battery_kwh"),2) if te.get("surplus_after_battery_kwh") is not None else None,
                round(te.get("export_ct_kwh"),2) if te.get("export_ct_kwh") is not None else None,
                round(te.get("pellet_heat_ct_kwh"),2) if te.get("pellet_heat_ct_kwh") is not None else None,
                round(te.get("advantage_heat_ct_kwh"),2) if te.get("advantage_heat_ct_kwh") is not None else None,te.get("reason"))
            bs=decision.inputs.get("buffer_strategy") or {}
            log.info("buffer strategy | mode=%s | deep_charge_allowed=%s | current_min_off=%s C | recommended_min_off=%s C | reason=%s",
                bs.get("name"),bs.get("deep_charge_allowed"),bs.get("current_min_off_c"),bs.get("recommended_min_off_c"),bs.get("reason"))
            log.info("price context | current=%.3f ct/kWh | next_slots=%s | min=%s | avg=%s | max=%s | class=%s",
                decision.inputs.get("market_current_spot_ct") or 0,
                decision.inputs.get("market_future_slots"),
                round(decision.inputs.get("market_future_spot_min_ct"),3) if decision.inputs.get("market_future_spot_min_ct") is not None else None,
                round(decision.inputs.get("market_future_spot_avg_ct"),3) if decision.inputs.get("market_future_spot_avg_ct") is not None else None,
                round(decision.inputs.get("market_future_spot_max_ct"),3) if decision.inputs.get("market_future_spot_max_ct") is not None else None,
                decision.inputs.get("market_price_class"))
            log.info("price timing | cheapest_in=%s h | most_expensive_in=%s h",decision.inputs.get("market_hours_to_min"),decision.inputs.get("market_hours_to_max"))
            log.info("profile detect | profile=%s | reason=%s",decision.inputs.get("detected_profile"),decision.inputs.get("profile_reason"))
            log.info("shadow | action=%s | confidence=%s | reason=%s",decision.action,decision.confidence,decision.reason)
            if telemetry_url and time.time()-last_telemetry>=telemetry_interval:
                status,count=send_telemetry(telemetry_url,server_cfg.get("installation_id",options.get("installation_id","pilot-local")),model,decision)
                if isinstance(status,int):
                    TELEMETRY_STATUS.write_text(json.dumps({"connected":True,"last_success":decision.timestamp,"points":count,"status":status,"endpoint":telemetry_url}),encoding="utf-8");log.info("telemetry | sent=%d | status=%d | endpoint=%s",count,status,telemetry_url)
                else:
                    TELEMETRY_STATUS.write_text(json.dumps({"connected":False,"last_error":str(status),"endpoint":telemetry_url}),encoding="utf-8");log.warning("telemetry | send failed | endpoint=%s | error=%s",telemetry_url,status)
                last_telemetry=time.time()
            if time.time()-last>300:snapshot(client,mappings);last=time.time()
        time.sleep(interval)

if __name__=="__main__":main()
