"""Vendor-neutral contracts for INS-EI device plugins."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Protocol

@dataclass(slots=True)
class DeviceIdentity:
    manufacturer: str
    family: str
    model: str | None = None
    firmware: str | None = None
    serial: str | None = None
    profile: str = "generic"
    capabilities: set[str] = field(default_factory=set)
    raw: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class PluginPoint:
    component_id: str
    point: str
    value: Any
    unit: str | None = None
    source: str | None = None

@dataclass(slots=True)
class PluginProbe:
    identity: DeviceIdentity
    points: list[PluginPoint] = field(default_factory=list)
    unmapped: dict[str, Any] = field(default_factory=dict)

class DevicePlugin(Protocol):
    plugin_id: str
    manufacturer: str
    def detect(self) -> DeviceIdentity: ...
    def read(self) -> PluginProbe: ...
