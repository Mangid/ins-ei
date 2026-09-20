"""Mapping configuration helpers with validation, catalog defaults and bulk import."""
from dataclasses import dataclass
from typing import Any

from ins_ei.catalog import point_spec
from ins_ei.model import DataRole
from .home_assistant import EntityMapping, FreshnessPolicy


@dataclass(slots=True)
class MappingValidation:
    errors: list[str]
    warnings: list[str]


def _normalize_point(value: str) -> str:
    return value.strip().replace("\\.", ".")


def _expanded_items(config: dict[str, Any]) -> list[dict[str, Any]]:
    items = list(config.get("mappings", []))
    for line in config.get("bulk_mappings", []):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        parts = [part.strip() for part in text.split("|")]
        if len(parts) != 3:
            raise ValueError(
                f"bulk mapping must be component|point|entity_id: {line}"
            )
        items.append(
            {
                "component_id": parts[0],
                "point": parts[1],
                "entity_id": parts[2],
            }
        )
    return items


def validate_mapping_config(config: dict[str, Any]) -> MappingValidation:
    errors: list[str] = []
    warnings: list[str] = []
    seen_entities: dict[str, int] = {}
    seen_points: dict[str, int] = {}

    try:
        items = _expanded_items(config)
    except ValueError as exc:
        return MappingValidation([str(exc)], [])

    for number, item in enumerate(items, start=1):
        component_id = item["component_id"].strip()
        point = _normalize_point(item["point"])
        entity_id = item["entity_id"].strip()
        logical = f"{component_id}.{point}"

        if (
            item["component_id"] != component_id
            or item["point"] != point
            or item["entity_id"] != entity_id
        ):
            warnings.append(
                f"mapping {number}: whitespace/escaping normalized for {logical}"
            )

        if point_spec(component_id, point) is None:
            errors.append(f"mapping {number}: unsupported point {logical}")

        if logical in seen_points:
            errors.append(
                f"mapping {number}: duplicate abstract point {logical} "
                f"(already mapping {seen_points[logical]})"
            )
        else:
            seen_points[logical] = number

        if entity_id in seen_entities:
            errors.append(
                f"mapping {number}: entity {entity_id} already used by "
                f"mapping {seen_entities[entity_id]}"
            )
        else:
            seen_entities[entity_id] = number

    return MappingValidation(errors, warnings)


def mappings_from_dict(config: dict[str, Any]) -> list[EntityMapping]:
    result: list[EntityMapping] = []

    for item in _expanded_items(config):
        component_id = item["component_id"].strip()
        point = _normalize_point(item["point"])
        entity_id = item["entity_id"].strip()
        spec = point_spec(component_id, point)

        default_freshness = spec.freshness if spec else "NORMAL"
        default_role = spec.role if spec else DataRole.MONITORING.value

        result.append(
            EntityMapping(
                component_id=component_id,
                point=point,
                entity_id=entity_id,
                unit=item.get("unit") or (spec.unit if spec else None),
                role=DataRole(item.get("role", default_role)),
                scale=float(item.get("scale", 1.0)),
                invert_sign=bool(item.get("invert_sign", False)),
                freshness=FreshnessPolicy(
                    item.get("freshness", default_freshness)
                ),
                max_age_seconds=item.get("max_age_seconds"),
            )
        )

    return result
