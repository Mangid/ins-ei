"""Traceable read-only SHADOW decision layer for INS-EI pilot."""
from dataclasses import dataclass,asdict
from datetime import datetime,timezone
from typing import Any
from ins_ei.model import DataQuality

@dataclass(slots=True)
class ShadowDecision:
    action:str
    reason:str
    confidence:str
    inputs:dict[str,Any]
    alternatives:list[dict[str,Any]]
    guards:list[str]
    timestamp:str
    def to_dict(self):return asdict(self)

def _point(site,kind,name):
    for component in site.components_by_kind(kind):
        point=component.point(name)
        if point:return point
    return None

def _usable(point):
    return point is not None and point.quality==DataQuality.GOOD and point.value is not None

def _input(point):
    return {"value":point.value if point else None,"unit":point.unit if point else None,"quality":point.quality.value if point else "MISSING","source":point.source if point else None}

def _market_prices(site):
    components=site.components_by_kind("MARKET")
    if not components:return None,None,"MARKET fehlt"
    cfg=components[0].config or {}
    spot=_point(site,"MARKET","spot_price")
    def price(side,point_name):
        tariff=cfg.get(side,{})
        mode=tariff.get("mode","DYNAMIC")
        if mode=="STATIC":return float(tariff.get("static_ct",0))
        if mode=="HA_SENSOR":
            p=_point(site,"MARKET",point_name)
            return float(p.value) if _usable(p) else None
        if not _usable(spot):return None
        base=float(spot.value)*100.0
        adjusted=(base+float(tariff.get("markup_ct",0)))*(1.0+float(tariff.get("adjust_percent",0))/100.0)
        return adjusted*(1.0+float(tariff.get("vat_percent",0))/100.0)
    return price("import","import_price"),price("export","export_price"),None

def evaluate(site,market_series=None):
    pv=_point(site,"PV","power");grid=_point(site,"GRID","power");soc=_point(site,"BATTERY","soc")
    batt=_point(site,"BATTERY","power");pth=_point(site,"POWER_TO_HEAT","electrical_power")
    buffer=_point(site,"BUFFER","temperature_upper");dhw=_point(site,"DHW","temperature")
    import_ct,export_ct,market_error=_market_prices(site)
    market_series=market_series or []
    now_utc=datetime.now(timezone.utc)
    future=[]
    for slot in market_series:
        try:
            start=datetime.fromisoformat(slot["start_time"]).astimezone(timezone.utc)
            if start>=now_utc:future.append(float(slot["price_per_kwh"])*100.0)
        except (KeyError,TypeError,ValueError):pass
    next_prices=future[:24]
    next_min=min(next_prices) if next_prices else None
    next_max=max(next_prices) if next_prices else None
    next_avg=sum(next_prices)/len(next_prices) if next_prices else None
    forecast_pv=_point(site,"FORECAST","pv_today");forecast_load=_point(site,"FORECAST","consumption_today")
    forecast_pv_hour=_point(site,"FORECAST","pv_current_hour");forecast_load_hour=_point(site,"FORECAST","consumption_current_hour")
    inputs={"pv_power":_input(pv),"grid_power":_input(grid),"battery_soc":_input(soc),"battery_power":_input(batt),"power_to_heat":_input(pth),"buffer_upper":_input(buffer),"dhw_temperature":_input(dhw),"forecast_pv_today":_input(forecast_pv),"forecast_consumption_today":_input(forecast_load),"forecast_pv_current_hour":_input(forecast_pv_hour),"forecast_consumption_current_hour":_input(forecast_load_hour),"market_spot_price":_input(_point(site,"MARKET","spot_price")),"import_price_ct_kwh":import_ct,"export_price_ct_kwh":export_ct,"market_future_slots":len(next_prices),"market_future_spot_min_ct":next_min,"market_future_spot_max_ct":next_max,"market_future_spot_avg_ct":next_avg,"market_current_spot_ct":(float(_point(site,"MARKET","spot_price").value)*100.0 if _usable(_point(site,"MARKET","spot_price")) else None)}
    now=datetime.now(timezone.utc).isoformat();guards=[];alternatives=[]
    if market_error:guards.append(market_error)
    if import_ct is None or export_ct is None:guards.append("Tarifpreis aktuell nicht vollständig verfügbar")
    critical=[("grid.power",grid),("pv.power",pv),("battery.soc",soc)]
    missing=[name for name,point in critical if not _usable(point)]
    if missing:
        guards.append("Kritische Datenqualität unzureichend")
        return ShadowDecision("OBSERVE_ONLY","Keine Optimierungsentscheidung, weil kritische Eingangsdaten fehlen oder nicht GOOD sind: "+", ".join(missing),"LOW",inputs,alternatives,guards,now)

    pv_w=float(pv.value);grid_w=float(grid.value);soc_pct=float(soc.value)
    pv_fc=float(forecast_pv.value) if _usable(forecast_pv) else None
    load_fc=float(forecast_load.value) if _usable(forecast_load) else None
    forecast_balance=(pv_fc-load_fc) if pv_fc is not None and load_fc is not None else None
    inputs["forecast_balance_today_kwh"]=forecast_balance
    if forecast_balance is None:guards.append("Tagesprognose PV/Verbrauch nicht vollständig verfügbar")
    if not _usable(pth):guards.append("Power-to-Heat aktuell nicht belastbar verfügbar")
    if not _usable(buffer):guards.append("Puffertemperatur für thermische Bewertung nicht verfügbar")
    if not _usable(dhw):guards.append("Warmwassertemperatur für thermische Bewertung nicht verfügbar")

    # First forecast + price aware battery policy. It remains SHADOW-only:
    # no actuator commands are emitted from this module.
    forecast_surplus=forecast_balance is not None and forecast_balance>2.0
    forecast_deficit=forecast_balance is not None and forecast_balance<-2.0
    cheap_import=import_ct is not None and import_ct<12.0
    valuable_export=export_ct is not None and export_ct>15.0

    if grid_w>100:
        alternatives.append({"action":"GRID_IMPORT","reason":"Netzbezug unverändert zulassen"})
        if forecast_deficit:
            alternatives.append({"action":"PRESERVE_BATTERY","reason":f"Tagesprognose zeigt {abs(forecast_balance):.2f} kWh erwartetes Energiedefizit"})
        if soc_pct<=20:
            action="GRID_IMPORT"
            reason=f"Netzbezug {grid_w:.0f} W bei SOC {soc_pct:.1f} %. SOC-Schutz hat Vorrang."
            guards.append("SOC-Schutz")
        elif forecast_surplus and cheap_import:
            action="PRESERVE_BATTERY"
            reason=f"Netzbezug {grid_w:.0f} W, SOC {soc_pct:.1f} %, erwarteter Tagesüberschuss {forecast_balance:.2f} kWh und Bezugspreis {import_ct:.2f} ct/kWh. SHADOW hält Batterieenergie für wertvollere Zeitfenster zurück."
        else:
            action="BATTERY_SUPPORT_LOAD"
            reason=f"Netzbezug {grid_w:.0f} W bei SOC {soc_pct:.1f} %. Batterie kann Bezug zu {import_ct:.2f} ct/kWh vermeiden." if import_ct is not None else f"Netzbezug {grid_w:.0f} W bei SOC {soc_pct:.1f} %. Batterie könnte den Netzbezug reduzieren."

    elif grid_w<-100:
        surplus=-grid_w
        alternatives.append({"action":"EXPORT_PV","reason":"PV-Überschuss einspeisen"})
        if valuable_export and soc_pct>40:
            action="EXPORT_PV"
            reason=f"PV-Überschuss ca. {surplus:.0f} W, Einspeisepreis {export_ct:.2f} ct/kWh und SOC {soc_pct:.1f} %. Verkauf ist im SHADOW-Modell wirtschaftlich interessant."
        elif soc_pct<95:
            action="CHARGE_BATTERY"
            extra=f" Tagesprognose: {forecast_balance:+.2f} kWh." if forecast_balance is not None else ""
            reason=f"PV-Überschuss ca. {surplus:.0f} W bei SOC {soc_pct:.1f} %. Batterie hat Aufnahmefähigkeit.{extra}"
        elif _usable(pth) and _usable(buffer):
            temp=float(buffer.value)
            if temp<70:
                action="POWER_TO_HEAT"
                reason=f"PV-Überschuss ca. {surplus:.0f} W, SOC {soc_pct:.1f} % und Puffer oben {temp:.1f} °C. Thermische Aufnahme ist plausibel."
            else:
                action="EXPORT_PV"
                reason=f"PV-Überschuss ca. {surplus:.0f} W, SOC {soc_pct:.1f} % und Puffer oben {temp:.1f} °C. Einspeisung bleibt die sichere Option."
                guards.append("Puffer-Temperaturschutz")
        else:
            action="EXPORT_PV"
            reason=f"PV-Überschuss ca. {surplus:.0f} W bei SOC {soc_pct:.1f} %. Keine belastbare thermische Aufnahme verfügbar."

    else:
        if forecast_surplus and cheap_import and soc_pct>20:
            action="PRESERVE_BATTERY"
            reason=f"Netzleistung {grid_w:.0f} W ist ausgeglichen. Für heute werden {forecast_balance:.2f} kWh Überschuss erwartet; aktueller Bezugspreis {import_ct:.2f} ct/kWh. Batterie wird im SHADOW-Modell nicht unnötig entladen."
        else:
            action="BALANCED"
            reason=f"Netzleistung {grid_w:.0f} W liegt innerhalb der ±100-W-Deadband. Kein Eingriff erforderlich."
        alternatives.append({"action":"NO_CHANGE","reason":"Aktuellen Anlagenzustand beibehalten"})

    confidence="HIGH" if not guards else "MEDIUM"
    return ShadowDecision(action,reason,confidence,inputs,alternatives,guards,now)
