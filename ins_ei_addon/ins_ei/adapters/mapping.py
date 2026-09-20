"""Mapping configuration helpers with validation and catalog defaults."""
from dataclasses import dataclass
from typing import Any
from ins_ei.model import DataRole
from ins_ei.catalog import point_spec
from .home_assistant import EntityMapping, FreshnessPolicy

@dataclass(slots=True)
class MappingValidation:
    errors:list[str]
    warnings:list[str]

def _expanded_items(config:dict[str,Any])->list[dict[str,Any]]:
    items=list(config.get("mappings",[]))
    for line in config.get("bulk_mappings",[]):
        text=line.strip()
        if not text or text.startswith("#"):
            continue
        parts=[part.strip() for part in text.split("|")]
        if len(parts)!=3:
            raise ValueError(f"bulk mapping must be component|point|entity_id: {line}")
        items.append({"component_id":parts[0],"point":parts[1],"entity_id":parts[2]})
    return items

def validate_mapping_config(config:dict[str,Any])->MappingValidation:
    errors=[];warnings=[];seen_entities={};seen_points={}
    try:\n        items=_expanded_items(config)\n    except ValueError as exc:\n        return MappingValidation([str(exc)],[])\n    for n,item in enumerate(items,start=1):
        cid=item["component_id"].strip()
        point=item["point"].strip().replace("\\\\.", ".").replace("\\.", ".")
        entity=item["entity_id"].strip()
        logical=f"{cid}.{point}"
        if item["component_id"] != cid or item["point"] != point or item["entity_id"] != entity:
            warnings.append(f"mapping {n}: surrounding whitespace normalized for {logical}")
        spec=point_spec(cid,point)
        if spec is None: errors.append(f"mapping {n}: unsupported point {logical}")
        if logical in seen_points: errors.append(f"mapping {n}: duplicate abstract point {logical} (already mapping {seen_points[logical]})")
        else: seen_points[logical]=n
        if entity in seen_entities: errors.append(f"mapping {n}: entity {entity} already used by mapping {seen_entities[entity]}")
        else: seen_entities[entity]=n
    return MappingValidation(errors,warnings)

def mappings_from_dict(config:dict[str,Any])->list[EntityMapping]:
    result=[]
    for i in _expanded_items(config):
        cid=i["component_id"].strip()
        point=i["point"].strip().replace("\\\\.", ".").replace("\\.", ".")
        spec=point_spec(cid,point)
        default_freshness=spec.freshness if spec else "NORMAL"
        default_role=spec.role if spec else DataRole.MONITORING.value
        result.append(EntityMapping(component_id=cid,point=point,entity_id=i["entity_id"].strip(),unit=i.get("unit") or (spec.unit if spec else None),role=DataRole(i.get("role",default_role)),scale=float(i.get("scale",1.0)),invert_sign=bool(i.get("invert_sign",False)),freshness=FreshnessPolicy(i.get("freshness",default_freshness)),max_age_seconds=i.get("max_age_seconds")))
    return result
