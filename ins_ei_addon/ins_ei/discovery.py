"""Read-only Home Assistant discovery with mapped/duplicate awareness."""
from dataclasses import dataclass
from typing import Any

@dataclass(slots=True)
class DiscoveryCandidate:
    entity_id: str
    state: Any
    name: str | None
    unit: str | None
    device_class: str | None
    state_class: str | None
    suggested_domain: str | None
    suggested_point: str | None
    score: int
    mapped_to: str | None = None

KEYWORDS = {
    "GRID": ("grid", "netz", "em540", "meter"),
    "PV": ("pv", "solar", "photovolta", "wechselrichter", "inverter"),
    "BATTERY": ("pylontech", "ess battery", "batteriespeicher"),
    "POWER_TO_HEAT": ("ac_thor", "ac thor", "ohmpilot", "heizstab", "power to heat"),
    "BUFFER": ("buffer", "puffer"),
    "DHW": ("hot_water", "warmwasser", "boiler", "dhw"),
    "HEAT_PUMP": ("heat_pump", "heat pump", "wärmepumpe", "waermepumpe", "knv"),
    "PELLET_BOILER": ("pellematic", "ökofen", "oekofen", "pellet"),
    "ROOM": ("raumtemperatur", "humidity", "feuchte"),
}
EXCLUDE_BATTERY = ("iphone", "ipad", "watch", "rauchmelder", "smoke", "remote", "phone")

def _suggest_point(domain: str | None, hay: str, device_class: str | None) -> str | None:
    if domain == "BATTERY":
        if "state_of_health" in hay or "state of health" in hay: return "soh"
        if "ladestand" in hay or " soc" in hay or device_class == "battery": return "soc"
        if "maximale zellspannung" in hay: return "max_cell_voltage"
        if "minimale zellspannung" in hay: return "min_cell_voltage"
        if "maximale zelltemperatur" in hay: return "max_cell_temperature"
        if "minimale zelltemperatur" in hay: return "min_cell_temperature"
        if device_class == "power" or "leistung" in hay: return "power"
        if device_class == "voltage" or "spannung" in hay: return "voltage"
        if device_class == "current" or "stromst" in hay: return "current"
        if device_class == "temperature" or "temperatur" in hay: return "temperature"
    if domain in ("GRID", "PV") and device_class == "power": return "power"
    if domain == "POWER_TO_HEAT" and device_class == "power": return "electrical_power"
    if domain in ("BUFFER", "DHW") and device_class == "temperature": return "temperature" if domain == "DHW" else None
    if domain == "ROOM":
        if device_class == "temperature": return "temperature"
        if device_class == "humidity": return "humidity"
    return None

def discover(states: list[dict[str, Any]], mapped: dict[str, str] | None = None) -> list[DiscoveryCandidate]:
    mapped = mapped or {}
    out = []
    for item in states:
        entity_id = item.get("entity_id", "")
        attrs = item.get("attributes") or {}
        name = attrs.get("friendly_name")
        hay = f"{entity_id} {name or ''}".lower()
        best_domain = None
        best_score = 0
        for domain, words in KEYWORDS.items():
            score = sum(2 for word in words if word in hay)
            if domain == "BATTERY" and any(word in hay for word in EXCLUDE_BATTERY):
                score = 0
            if score > best_score:
                best_domain, best_score = domain, score
        unit = attrs.get("unit_of_measurement")
        device_class = attrs.get("device_class")
        if best_domain and device_class in ("power", "energy", "voltage", "current", "temperature", "humidity", "battery"):
            best_score += 1
        if best_score:
            out.append(DiscoveryCandidate(
                entity_id=entity_id,
                state=item.get("state"),
                name=name,
                unit=unit,
                device_class=device_class,
                state_class=attrs.get("state_class"),
                suggested_domain=best_domain,
                suggested_point=_suggest_point(best_domain, hay, device_class),
                score=best_score,
                mapped_to=mapped.get(entity_id),
            ))
    return sorted(out, key=lambda candidate: (candidate.mapped_to is not None, -candidate.score, candidate.entity_id))
