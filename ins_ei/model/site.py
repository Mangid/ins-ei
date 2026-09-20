"""Top-level site model for one INS-EI installation."""
from dataclasses import dataclass, field
from typing import Any

from .component import Component
from .enums import OperatingMode, ThermalTopology


@dataclass(slots=True)
class SiteLocation:
    timezone: str
    latitude: float | None = None
    longitude: float | None = None
    elevation_m: float | None = None


@dataclass(slots=True)
class Connection:
    source: str
    target: str
    purpose: str | None = None
    target_zone: str | None = None


@dataclass(slots=True)
class ActionDefinition:
    id: str
    component_id: str
    action_type: str
    control_point: str
    restore_required: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Constraint:
    id: str
    target: str
    kind: str
    value: Any
    unit: str | None = None
    hard: bool = True


@dataclass(slots=True)
class SiteModel:
    installation_id: str
    location: SiteLocation
    mode: OperatingMode = OperatingMode.SHADOW
    thermal_topology: ThermalTopology | None = None
    components: dict[str, Component] = field(default_factory=dict)
    connections: list[Connection] = field(default_factory=list)
    actions: list[ActionDefinition] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)

    def add_component(self, component: Component) -> None:
        if component.id in self.components:
            raise ValueError(f"Duplicate component id: {component.id}")
        self.components[component.id] = component

    def component(self, component_id: str) -> Component | None:
        return self.components.get(component_id)

    def components_by_kind(self, kind: str) -> list[Component]:
        return [c for c in self.components.values() if c.kind == kind and c.enabled]

    def validate(self) -> list[str]:
        errors: list[str] = []
        component_ids = set(self.components)
        for connection in self.connections:
            if connection.source not in component_ids:
                errors.append(f"Unknown connection source: {connection.source}")
            if connection.target not in component_ids:
                errors.append(f"Unknown connection target: {connection.target}")
        for action in self.actions:
            if action.component_id not in component_ids:
                errors.append(f"Unknown action component: {action.component_id}")
        return errors
