from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class SensorSample:
    """A single captured blob (frame or video chunk) ready for storage."""

    data: bytes
    timestamp: datetime
    time_end: datetime | None
    sensor_type: str
    sensor_name: str
    lat: float
    lon: float
    altitude: float
    bucket: str


@dataclass
class StoredRecord:
    """Metadata of a record already persisted in SpatiaLite."""

    id: int
    timestamp: datetime
    time_end: datetime | None
    sensor_type: str
    sensor_name: str
    robot_id: str
    file_id: str
    bucket: str
    altitude: float
    heading: float
    pitch: float
    roll: float
    lat: float
    lon: float


@dataclass(frozen=True)
class SpatialQuery:
    """Parameters for a spatial bounding-box query."""

    bbox: tuple[float, float, float, float]  # (lon_min, lat_min, lon_max, lat_max)
    time_start: datetime
    time_end: datetime
    sensor_type: str | None = None


@dataclass
class QueryResult:
    """A single result entry returned to a querying client."""

    metadata: StoredRecord
    data_b64: str
