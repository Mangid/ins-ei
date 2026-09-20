"""Generic normalized data point used throughout INS-EI."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .enums import DataQuality, DataRole


@dataclass(slots=True)
class DataPoint:
    key: str
    value: Any = None
    unit: str | None = None
    timestamp: datetime | None = None
    quality: DataQuality = DataQuality.UNAVAILABLE
    source: str | None = None
    role: DataRole = DataRole.MONITORING
    writable: bool = False

    @property
    def available(self) -> bool:
        return self.value is not None and self.quality in {DataQuality.GOOD, DataQuality.STALE}
