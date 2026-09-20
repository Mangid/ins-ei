"""Base component definitions."""
from dataclasses import dataclass, field
from typing import Any

from .data_point import DataPoint


@dataclass(slots=True)
class Component:
    id: str
    kind: str
    name: str | None = None
    enabled: bool = True
    points: dict[str, DataPoint] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    capabilities: set[str] = field(default_factory=set)

    def point(self, key: str) -> DataPoint | None:
        return self.points.get(key)

    def available_points(self) -> dict[str, DataPoint]:
        return {key: point for key, point in self.points.items() if point.available}
