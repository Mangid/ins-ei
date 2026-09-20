"""Mapping configuration helpers."""
from typing import Any
from ins_ei.model import DataRole
from .home_assistant import EntityMapping, FreshnessPolicy

def mappings_from_dict(config:dict[str,Any])->list[EntityMapping]:
    return [EntityMapping(component_id=i["component_id"],point=i["point"],entity_id=i["entity_id"],unit=i.get("unit"),role=DataRole(i.get("role",DataRole.MONITORING.value)),scale=float(i.get("scale",1.0)),invert_sign=bool(i.get("invert_sign",False)),freshness=FreshnessPolicy(i.get("freshness","NORMAL")),max_age_seconds=i.get("max_age_seconds")) for i in config.get("mappings",[])]
