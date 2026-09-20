"""Shared enums for the vendor-neutral INS-EI model."""
from enum import Enum


class DataQuality(str, Enum):
    GOOD = "GOOD"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"


class DataRole(str, Enum):
    OPTIMIZATION = "OPTIMIZATION"
    MONITORING = "MONITORING"
    DIAGNOSTIC = "DIAGNOSTIC"


class Availability(str, Enum):
    SUPPORTED = "SUPPORTED"
    AVAILABLE = "AVAILABLE"
    USED = "USED"


class ThermalTopology(str, Enum):
    DIRECT = "DIRECT"
    BUFFER = "BUFFER"
    COMBINED_STORAGE = "COMBINED_STORAGE"


class OperatingMode(str, Enum):
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"
    OFF = "OFF"
    FAILOVER_LOCKED = "FAILOVER_LOCKED"
