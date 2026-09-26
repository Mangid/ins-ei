"""INS-EI runtime: persistent installation model + collector."""
from __future__ import annotations
import json,logging,os,time
from datetime import datetime,timezone
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

OPTIONS=Path("/data/options.json");UI=Path("/data/ui_mappings.json");VRM_SERIES=Path("/data/vrm_forecast_series.json");DAY_PLAN=Path("/data/day_plan.json");PLAN_HISTORY=Path("/data/plan_history");DISC=Path("/data/discovery.json");COMPONENTS=Path("/data/components.json");SITE=Path("/data/site_model.json");SHADOW=Path("/data/shadow_decision.json");MARKET=Path("/data/market.json");MARKET_SERIES=Path("/data/market_series.json");STRATEGY=Path("/data/strategy.json");SERVER=Path("/data/server.json");TELEMETRY_STATUS=Path("/data/telemetry_status.json");PLUGINS=Path("/data/plugins.json");ASSIST=Path("/data/assisted_thermal.json")
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

def read_vrm_plugin(config,log):
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
        log.info("plugin | vrm | profile=forecast_hours |  pv_slots=%d | load_slots=%d | common_slots=%d | pv_kwh=%.3f | load_kwh=%.3f",len(pv),len(load_fc),len(slots),sum(x["pv_kwh"] for x in slots),sum(x["load_kwh"] for x in slots))
        return slots
    except Exception as exc:
        log.warning("plugin | vrm failed | %s",exc);return None

def build_day_plan_inputs(vrm_slots,market_series,market_cfg):
    """Normalize future VRM + market data into vendor-neutral hourly planner slots."""
    if not vrm_slots or not market_series:return []
    def hour_utc_from_ms(ms):
        return datetime.fromtimestamp(ms/1000,timezone.utc).replace(minute=0,second=0,microsecond=0)
    prices={}
    for x in market_series:
        try:
            dt=datetime.fromisoformat(x["start_time"].replace("Z","+00:00")).astimezone(timezone.utc).replace(minute=0,second=0,microsecond=0)
            prices[dt]=float(x["price_per_kwh"])*100.0
        except (KeyError,TypeError,ValueError):continue
    imp=market_cfg.get("import") or {};exp=market_cfg.get("export") or {}
    def tariff(raw,side):
        cfg=imp if side=="import" else exp
        if cfg.get("mode")=="STATIC":return float(cfg.get("static_ct",0))
        if cfg.get("mode")=="HA_SENSOR":return None
        value=(raw+float(cfg.get("markup_ct",0)))*(1+float(cfg.get("adjust_percent",0))/100)
        return value*(1+float(cfg.get("vat_percent",0))/100)
    now=datetime.now(timezone.utc)
    result=[]
    for x in vrm_slots:
        h=hour_utc_from_ms(x["timestamp_ms"])
        if h < now.replace(minute=0,second=0,microsecond=0):continue
        raw=prices.get(h)
        if raw is None:continue
        result.append({"time":h.isoformat(),"pv_kwh":round(float(x["pv_kwh"]),4),"load_kwh":round(float(x["load_kwh"]),4),
            "spot_ct_kwh":round(raw,4),"buy_ct_kwh":round(tariff(raw,"import"),4),"sell_ct_kwh":round(tariff(raw,"export"),4)})
    return result

def build_shadow_day_plan(slots,decision):
    """Greedy auditable V1 planner. Read-only; carries battery state slot by slot."""
    if not slots:return {"status":"NO_SLOTS","slots":[]}
    be=decision.inputs.get("battery_energy") or {}
    cap=float(be.get("capacity_kwh") or 0);min_soc=float(be.get("min_soc_percent") or 0);max_soc=float(be.get("max_soc_percent") or 100)
    soc_point=decision.inputs.get("battery_soc") or {};soc=float(soc_point.get("value") or min_soc)
    energy=cap*soc/100;emin=cap*min_soc/100;emax=cap*max_soc/100
    pellet=float(decision.inputs.get("pellet_heat_cost_ct_kwh") or 0)
    rows=[]
    for x in slots:
        pv=x["pv_kwh"];load=x["load_kwh"];direct=min(pv,load);pv_left=pv-direct;load_left=load-direct
        batt_to_load=min(load_left,max(energy-emin,0));energy-=batt_to_load;load_left-=batt_to_load
        pv_to_batt=min(pv_left,max(emax-energy,0));energy+=pv_to_batt;pv_left-=pv_to_batt
        econ="PV_TO_HEAT" if pellet and x["sell_ct_kwh"]+1.0<=pellet else "EXPORT"
        heat_kwh=pv_left if econ=="PV_TO_HEAT" else 0.0
        export_kwh=pv_left if econ=="EXPORT" else 0.0
        rows.append({**x,"pv_to_load_kwh":round(direct,4),"pv_to_battery_kwh":round(pv_to_batt,4),
            "battery_to_load_kwh":round(batt_to_load,4),"grid_import_kwh":round(load_left,4),
            "surplus_after_battery_kwh":round(pv_left,4),"pv_to_heat_candidate_kwh":round(heat_kwh,4),
            "pv_export_candidate_kwh":round(export_kwh,4),"export_revenue_candidate_ct":round(export_kwh*x["sell_ct_kwh"],2),
            "thermal_economic_action":econ,
            "soc_after_percent":round((energy/cap*100) if cap else soc,1)})
    return {"status":"SHADOW_V1","generated_at":datetime.now(timezone.utc).isoformat(),"slots":rows,
        "summary":{"pv_to_heat_candidate_kwh":round(sum(x["pv_to_heat_candidate_kwh"] for x in rows),4),
        "pv_export_candidate_kwh":round(sum(x["pv_export_candidate_kwh"] for x in rows),4),
        "export_revenue_candidate_ct":round(sum(x["export_revenue_candidate_ct"] for x in rows),2)}}

def dhw_transfer_shadow(model,decision):
    def good(kind,name):
        for c in model.components_by_kind(kind):
            p=c.point(name)
            if p is not None and p.value is not None and p.quality.value=="GOOD":return p
        return None
    upper=good("BUFFER","temperature_upper");dhw=good("DHW","temperature");cmd=good("DHW","one_time_charge")
    if upper is None or dhw is None:
        return {"recommendation":"HOLD","confidence":"LOW","current":cmd.value if cmd else None,"reason":"Puffer- oder Warmwasserdaten fehlen."}
    req=(decision.inputs.get("strategy") or {}).get("requirements") or {};minimum=float(req.get("dhw_min_c",50.0))
    upper_c=float(upper.value);dhw_c=float(dhw.value);delta=upper_c-dhw_c
    if dhw_c<=minimum and delta>=5.0:
        return {"recommendation":"HEAT_ONE","confidence":"HIGH","current":cmd.value if cmd else None,"reason":f"WW {dhw_c:.1f} C <= {minimum:.1f} C und Puffer oben {upper_c:.1f} C bietet {delta:.1f} K Temperaturvorsprung."}
    if dhw_c<=minimum:
        return {"recommendation":"HOLD","confidence":"HIGH","current":cmd.value if cmd else None,"reason":f"WW {dhw_c:.1f} C niedrig, aber Puffer oben {upper_c:.1f} C bietet nur {delta:.1f} K Vorsprung; Pufferladung nicht sinnvoll."}
    return {"recommendation":"HOLD","confidence":"MEDIUM","current":cmd.value if cmd else None,"reason":f"WW {dhw_c:.1f} C liegt über Mindestwert {minimum:.1f} C."}

def boiler_permission_shadow(model,decision,day_plan):
    def good(kind,name):
        for c in model.components_by_kind(kind):
            p=c.point(name)
            if p is not None and p.value is not None and p.quality.value=="GOOD": return p
        return None
    upper=good("BUFFER","temperature_upper");dhw=good("DHW","temperature");mode=good("PELLET_BOILER","operating_mode")
    current_mode=mode.value if mode else None
    if upper is None or dhw is None:
        return {"permission":"ALLOW","current_mode":current_mode,"confidence":"LOW","reason":"Fail-safe: Puffer- oder Warmwasserdaten fehlen."}
    req=(decision.inputs.get("strategy") or {}).get("requirements") or {}
    dhw_min=float(req.get("dhw_min_c",50.0));dhw_c=float(dhw.value);upper_c=float(upper.value)
    targets=[];active=False
    for c in model.components_by_kind("HEATING_CIRCUIT"):
        t=c.point("target_flow_temperature");p=c.point("pump_state")
        if t is not None and t.value is not None and t.quality.value=="GOOD": targets.append(float(t.value))
        if p is not None and p.value is not None and p.quality.value=="GOOD" and str(p.value).strip().lower() in ("on","ein","true","1","running","heating","heat"): active=True
    required=max(targets+[0])+5.0 if active else 0.0
    if dhw_c<=dhw_min:
        return {"permission":"ALLOW","current_mode":current_mode,"confidence":"HIGH","reason":f"Warmwasser {dhw_c:.1f} C erreicht Mindestwert {dhw_min:.1f} C nicht sicher."}
    if active and upper_c<=required:
        return {"permission":"ALLOW","current_mode":current_mode,"confidence":"HIGH","reason":f"Heizkreise aktiv; Puffer oben {upper_c:.1f} C liegt an Reservegrenze {required:.1f} C."}
    pv_heat=float(((day_plan or {}).get("summary") or {}).get("pv_to_heat_candidate_kwh") or 0)
    if pv_heat>=2.0:
        return {"permission":"BLOCK","current_mode":current_mode,"confidence":"MEDIUM","reason":f"Puffer oben {upper_c:.1f} C und WW {dhw_c:.1f} C ausreichend; {pv_heat:.2f} kWh wirtschaftliche PV-Waerme geplant."}
    # Fallback for installations without an hourly planner series yet:
    # use only the generic FORECAST daily PV signal, never invent hourly slots.
    fpv=decision.inputs.get("forecast_pv_today") or {}
    try: pv_day=float(fpv.get("value")) if fpv.get("quality")=="GOOD" else None
    except (TypeError,ValueError): pv_day=None
    if pv_day is not None and pv_day>=15.0:
        return {"permission":"BLOCK","current_mode":current_mode,"confidence":"LOW","reason":f"Kein Stundenfahrplan vorhanden, aber FORECAST erwartet {pv_day:.1f} kWh PV heute. Thermische Versorgung aktuell ausreichend; Pelletkessel konservativ zurueckhalten."}
    return {"permission":"ALLOW","current_mode":current_mode,"confidence":"MEDIUM","reason":f"Nur {pv_heat:.2f} kWh wirtschaftliche PV-Waerme geplant und kein starkes Tages-PV-Signal."}

def apply_assisted_thermal(plugin_cfg,bp,dw,log):
    o=plugin_cfg.get("oekofen") or {}
    if not o.get("thermal_assist_enabled"):return
    plugin=_PLUGIN_CACHE.get("oekofen")
    if plugin is None:return
    state=load(ASSIST,{"boiler_owned":False,"dhw_last_request":0})
    now=time.time()
    current=str(bp.get("current_mode")).strip().lower()
    try:
        if bp.get("permission")=="BLOCK" and current in ("1","1.0","auto") and not state.get("boiler_owned"):
            plugin.set_boiler_mode(0);state["boiler_owned"]=True;state["boiler_blocked_at"]=now
            log.warning("assisted thermal | actuator=pe1.mode | command=0 | ownership=INS_EI | reason=%s",bp.get("reason"))
        elif bp.get("permission")=="ALLOW" and state.get("boiler_owned"):
            plugin.set_boiler_mode(1);state["boiler_owned"]=False
            log.warning("assisted thermal | actuator=pe1.mode | command=1 | ownership=RELEASED | reason=%s",bp.get("reason"))
        if dw.get("recommendation")=="HEAT_ONE" and str(dw.get("current")).lower() not in ("true","1","on") and now-float(state.get("dhw_last_request",0))>=900:
            plugin.set_dhw_once(True);state["dhw_last_request"]=now
            log.warning("assisted thermal | actuator=ww1.heat_once | command=true | cooldown=900s | reason=%s",dw.get("reason"))
    except Exception as exc:
        log.error("assisted thermal | write failed | %s",exc)
    ASSIST.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")

def archive_day_plan(day_plan,log):
    """Persist immutable daily baseline plus changed plan revisions."""
    if not day_plan or not day_plan.get("slots"):return
    PLAN_HISTORY.mkdir(parents=True,exist_ok=True)
    local_now=datetime.now().astimezone();day=local_now.strftime("%Y-%m-%d")
    folder=PLAN_HISTORY/day;folder.mkdir(parents=True,exist_ok=True)
    # generated_at changes every cycle and must not create a fake revision.
    comparable=dict(day_plan);comparable.pop("generated_at",None)
    payload=json.dumps(comparable,ensure_ascii=False,sort_keys=True)
    baseline=folder/"baseline.json"
    if not baseline.exists():
        baseline.write_text(json.dumps(day_plan,ensure_ascii=False,indent=2),encoding="utf-8")
        log.info("day planner archive | baseline=%s | slots=%d",day,len(day_plan.get("slots",[])))
    latest=folder/"latest.json"
    previous=latest.read_text(encoding="utf-8") if latest.exists() else None
    if previous!=payload:
        stamp=local_now.strftime("%H%M%S")
        revision=folder/f"revision_{stamp}.json"
        revision.write_text(json.dumps(day_plan,ensure_ascii=False,indent=2),encoding="utf-8")
        latest.write_text(payload,encoding="utf-8")
        log.info("day planner archive | revision=%s | slots=%d",revision.name,len(day_plan.get("slots",[])))

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
                read_vrm_plugin(plugin_cfg,log)
            log.info("collector | read=%d good=%d stale=%d unavailable=%d plugin_points=%d",result.read,result.good,result.stale,result.unavailable,plugin_points)
            if result.stale or result.unavailable:
                for component in model.components.values():
                    for point_name,point in component.points.items():
                        if point.quality.value in ("STALE","UNAVAILABLE"):
                            log.info("collector issue | %s.%s | quality=%s | value=%s %s | source=%s",component.id,point_name,point.quality.value,point.value,point.unit or "",point.source)
            decision=shadow_evaluate(model,load(MARKET_SERIES,[]),strategy_cfg)
            market_series=load(MARKET_SERIES,[])
            vrm_series=load(VRM_SERIES,[])
            planner_inputs=build_day_plan_inputs(vrm_series,market_series,market_cfg)
            day_plan=build_shadow_day_plan(planner_inputs,decision)
            dw=dhw_transfer_shadow(model,decision)
            log.info("dhw transfer shadow | current=%s | recommendation=%s | confidence=%s | reason=%s",dw.get("current"),dw.get("recommendation"),dw.get("confidence"),dw.get("reason"))
            bp=boiler_permission_shadow(model,decision,day_plan)
            day_plan["boiler_permission"]=bp
            DAY_PLAN.write_text(json.dumps(day_plan,ensure_ascii=False,indent=2),encoding="utf-8")
            archive_day_plan(day_plan,log)
            log.info("boiler permission shadow | current_mode=%s | recommendation=%s | confidence=%s | reason=%s",bp.get("current_mode"),bp.get("permission"),bp.get("confidence"),bp.get("reason"))
            apply_assisted_thermal(plugin_cfg,bp,dw,log)
            if day_plan.get("slots"):
                rows=day_plan["slots"];log.info("day planner | status=%s | slots=%d | start=%s | end=%s | pv=%.2f kWh | load=%.2f kWh | grid_import=%.2f kWh | surplus_after_battery=%.2f kWh | end_soc=%.1f %%",
                    day_plan["status"],len(rows),rows[0]["time"],rows[-1]["time"],sum(x["pv_kwh"] for x in rows),sum(x["load_kwh"] for x in rows),
                    sum(x["grid_import_kwh"] for x in rows),sum(x["surplus_after_battery_kwh"] for x in rows),rows[-1]["soc_after_percent"])
                ps=day_plan.get("summary") or {}
                log.info("day planner economics | pv_to_heat=%s kWh | export=%s kWh | export_revenue=%s EUR | pellet_heat=%s ct/kWh",
                    ps.get("pv_to_heat_candidate_kwh"),ps.get("pv_export_candidate_kwh"),
                    round((ps.get("export_revenue_candidate_ct") or 0)/100,2),
                    round(decision.inputs.get("pellet_heat_cost_ct_kwh") or 0,2))

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
