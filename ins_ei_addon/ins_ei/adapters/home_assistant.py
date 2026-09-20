"""Home Assistant read-only adapter for INS-EI."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from ins_ei.model import DataPoint, DataQuality, DataRole

class HomeAssistantError(RuntimeError): pass

@dataclass(slots=True, frozen=True)
class EntityMapping:
    component_id: str
    point: str
    entity_id: str
    unit: str | None = None
    role: DataRole = DataRole.MONITORING
    scale: float = 1.0
    invert_sign: bool = False
    max_age_seconds: int | None = None

class HomeAssistantClient:
    def __init__(self, base_url: str, token: str, timeout: float = 10.0) -> None:
        self.base_url, self.token, self.timeout = base_url.rstrip("/"), token, timeout
    def _get_json(self, path: str) -> Any:
        req=Request(f"{self.base_url}{path}",headers={"Authorization":f"Bearer {self.token}","Content-Type":"application/json"})
        try:
            with urlopen(req, timeout=self.timeout) as response: return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc: raise HomeAssistantError(str(exc)) from exc
    def state(self, entity_id: str) -> dict[str, Any]: return self._get_json(f"/api/states/{entity_id}")

class HomeAssistantAdapter:
    def __init__(self, client: HomeAssistantClient) -> None: self.client=client
    def read(self, mapping: EntityMapping) -> DataPoint:
        key=f"{mapping.component_id}.{mapping.point}"
        try: state=self.client.state(mapping.entity_id)
        except HomeAssistantError: return DataPoint(key=key,unit=mapping.unit,quality=DataQuality.UNAVAILABLE,source=mapping.entity_id,role=mapping.role)
        raw=state.get("state"); changed=_parse_timestamp(state.get("last_updated")); updated=_parse_timestamp(state.get("last_updated")); quality=DataQuality.GOOD
        if raw in (None,"unknown","unavailable",""):
            return DataPoint(key=key,unit=mapping.unit,timestamp=changed,quality=DataQuality.UNAVAILABLE,source=mapping.entity_id,role=mapping.role)
        value: Any=raw
        try:
            value=float(raw)*mapping.scale
            if mapping.invert_sign: value*=-1
        except (TypeError,ValueError): pass
        if mapping.max_age_seconds and updated and (datetime.now(timezone.utc)-updated).total_seconds()>mapping.max_age_seconds: quality=DataQuality.STALE
        unit=mapping.unit or state.get("attributes",{}).get("unit_of_measurement")
        return DataPoint(key=key,value=value,unit=unit,timestamp=changed,quality=quality,source=mapping.entity_id,role=mapping.role)

def _parse_timestamp(value: str | None) -> datetime | None:
    if not value: return None
    try: return datetime.fromisoformat(value.replace("Z","+00:00"))
    except ValueError: return None
