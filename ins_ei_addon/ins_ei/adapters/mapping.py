"""Mapping configuration helpers with flexible bulk import."""
from dataclasses import dataclass
import re
from typing import Any
from ins_ei.catalog import point_spec
from ins_ei.model import DataRole
from .home_assistant import EntityMapping, FreshnessPolicy

@dataclass(slots=True)
class MappingValidation:
    errors:list[str]
    warnings:list[str]

def _normalize_point(value:str)->str:
    return value.strip().replace("\\.", ".")

def _parse_bulk_text(value:str)->list[dict[str,str]]:
    text=value.strip()
    text=re.sub(r"^bulk_mappings\s*:\s*","",text)
    pattern=r'["\']?([A-Za-z0-9_:-]+)\|([A-Za-z0-9_.\\-]+)\|([A-Za-z0-9_.-]+)["\']?'
    found=re.findall(pattern,text)
    return [{"component_id":a,"point":b,"entity_id":c} for a,b,c in found]

def _expanded_items(config:dict[str,Any])->list[dict[str,Any]]:
    items=list(config.get("mappings",[]))
    bulk=config.get("bulk_mappings",[])
    if isinstance(bulk,str):
        bulk=[bulk]
    for entry in bulk:
        parsed=_parse_bulk_text(str(entry))
        if not parsed and str(entry).strip():
            raise ValueError(f"could not parse bulk mapping input: {entry}")
        items.extend(parsed)
    return items

def validate_mapping_config(config:dict[str,Any])->MappingValidation:
    errors=[];warnings=[];seen_entities={};seen_points={}
    try:
        items=_expanded_items(config)
    except ValueError as exc:
        return MappingValidation([str(exc)],[])
    for number,item in enumerate(items,start=1):
        cid=item["component_id"].strip();point=_normalize_point(item["point"]);entity=item["entity_id"].strip();logical=f"{cid}.{point}"
        spec=point_spec(cid,point)
        if spec is None:errors.append(f"mapping {number}: unsupported point {logical}")
        if logical in seen_points:errors.append(f"mapping {number}: duplicate abstract point {logical} (already mapping {seen_points[logical]})")
        else:seen_points[logical]=number
        if entity in seen_entities:errors.append(f"mapping {number}: entity {entity} already used by mapping {seen_entities[entity]}")
        else:seen_entities[entity]=number
    return MappingValidation(errors,warnings)

def mappings_from_dict(config:dict[str,Any])->list[EntityMapping]:
    result=[]
    for item in _expanded_items(config):
        cid=item["component_id"].strip();point=_normalize_point(item["point"]);entity=item["entity_id"].strip();spec=point_spec(cid,point)
        default_freshness=spec.freshness if spec else "NORMAL";default_role=spec.role if spec else DataRole.MONITORING.value
        # Point Catalog semantics are authoritative. Legacy persisted freshness values
        # are intentionally ignored. max_age_seconds remains the only explicit
        # per-mapping freshness override.
        freshness=default_freshness
        result.append(EntityMapping(component_id=cid,point=point,entity_id=entity,unit=item.get("unit") or (spec.unit if spec else None),role=DataRole(item.get("role",default_role)),scale=float(item.get("scale",1.0)),invert_sign=bool(item.get("invert_sign",False)),freshness=FreshnessPolicy(freshness),max_age_seconds=item.get("max_age_seconds")))
    return result
