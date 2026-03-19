from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from core.models import SensorSample, SpatialQuery, StoredRecord

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sensor_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    time_end DATETIME,
    sensor_type TEXT NOT NULL,
    sensor_name TEXT NOT NULL,
    robot_id TEXT NOT NULL,
    file_id TEXT NOT NULL,
    bucket TEXT NOT NULL,
    altitude REAL,
    heading REAL,
    pitch REAL,
    roll REAL
);
"""

_CREATE_IDX_TIMESTAMP_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_timestamp ON sensor_metadata(timestamp);"
)

_CREATE_IDX_SENSOR_TYPE_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_sensor_type ON sensor_metadata(sensor_type);"
)

_INSERT_SQL = """
INSERT INTO sensor_metadata
    (timestamp, time_end, sensor_type, sensor_name, robot_id, file_id, bucket,
     altitude, heading, pitch, roll)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_ADD_GEOMETRY_COL_SQL = (
    "SELECT AddGeometryColumn('sensor_metadata', 'geometry', 4326, 'POINT', 'XY');"
)

_CREATE_SPATIAL_IDX_SQL = "SELECT CreateSpatialIndex('sensor_metadata', 'geometry');"

_UPDATE_GEOMETRY_SQL = """
UPDATE sensor_metadata SET geometry = MakePoint(?, ?, 4326) WHERE id = ?;
"""

_QUERY_SPATIAL_SQL = """
SELECT m.id, m.timestamp, m.time_end, m.sensor_type, m.sensor_name,
       m.robot_id, m.file_id, m.bucket, m.altitude, m.heading, m.pitch, m.roll,
       X(m.geometry), Y(m.geometry)
FROM sensor_metadata m
JOIN idx_sensor_metadata_geometry AS si ON m.id = si.pkid
WHERE si.xmin <= ? AND si.xmax >= ?
  AND si.ymin <= ? AND si.ymax >= ?
  AND m.timestamp >= ? AND m.timestamp <= ?
"""


class DBClient:
    """SpatiaLite database client for sensor metadata storage and spatial queries."""

    def __init__(self, db_path: Path | str = "robot_local.gpkg") -> None:
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        """Open the database connection, load SpatiaLite, and initialise schema."""
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.enable_load_extension(True)
        self._conn.load_extension("mod_spatialite")
        self._conn.enable_load_extension(False)

        # CRITICAL: WAL mode for concurrent reads/writes
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")

        # Initialise SpatiaLite metadata tables if needed
        self._conn.execute("SELECT InitSpatialMetaData(1);")

        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.commit()

        # Add geometry column (ignore error if already exists)
        try:
            self._conn.execute(_ADD_GEOMETRY_COL_SQL)
            self._conn.commit()
        except sqlite3.OperationalError:
            pass

        # Create spatial index (ignore error if already exists)
        try:
            self._conn.execute(_CREATE_SPATIAL_IDX_SQL)
            self._conn.commit()
        except sqlite3.OperationalError:
            pass

        self._conn.execute(_CREATE_IDX_TIMESTAMP_SQL)
        self._conn.execute(_CREATE_IDX_SENSOR_TYPE_SQL)
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _validate_coords(self, lon: float, lat: float) -> bool:
        """Check that coordinates are valid WGS84 (D-01)."""
        if not (-180.0 <= lon <= 180.0):
            logger.warning("Invalid longitude %.6f — must be in [-180, 180]", lon)
            return False
        if not (-90.0 <= lat <= 90.0):
            logger.warning("Invalid latitude %.6f — must be in [-90, 90]", lat)
            return False
        return True

    def _insert_batch_sync(
        self, records: list[SensorSample], robot_id: str
    ) -> list[int]:
        """Bulk-insert sensor metadata records into SpatiaLite."""
        assert self._conn is not None
        inserted_ids: list[int] = []
        cursor = self._conn.cursor()
        cursor.execute("BEGIN TRANSACTION;")

        for rec in records:
            if not self._validate_coords(rec.lon, rec.lat):
                continue

            ts = rec.timestamp.isoformat()
            te = rec.time_end.isoformat() if rec.time_end else None
            file_id = f"{rec.sensor_name}_{int(rec.timestamp.timestamp() * 1_000_000)}"

            cursor.execute(
                _INSERT_SQL,
                (
                    ts,
                    te,
                    rec.sensor_type,
                    rec.sensor_name,
                    robot_id,
                    file_id,
                    rec.bucket,
                    rec.altitude,
                    0.0,  # heading — not provided by SensorSample
                    0.0,  # pitch
                    0.0,  # roll
                ),
            )
            row_id = cursor.lastrowid
            cursor.execute(_UPDATE_GEOMETRY_SQL, (rec.lon, rec.lat, row_id))
            inserted_ids.append(row_id)

        self._conn.commit()
        return inserted_ids

    async def insert_batch(
        self, records: list[SensorSample], robot_id: str
    ) -> list[int]:
        """Async wrapper for bulk-inserting sensor metadata."""
        return await asyncio.to_thread(self._insert_batch_sync, records, robot_id)

    def _query_spatial_sync(self, query: SpatialQuery) -> list[StoredRecord]:
        """Query sensor metadata within a spatial bounding box."""
        assert self._conn is not None
        lon_min, lat_min, lon_max, lat_max = query.bbox

        sql = _QUERY_SPATIAL_SQL
        params: list[object] = [
            lon_max,
            lon_min,
            lat_max,
            lat_min,
            query.time_start.isoformat(),
            query.time_end.isoformat(),
        ]

        if query.sensor_type is not None:
            sql += "  AND m.sensor_type = ?\n"
            params.append(query.sensor_type)

        cursor = self._conn.execute(sql, params)
        rows = cursor.fetchall()

        return [
            StoredRecord(
                id=row[0],
                timestamp=row[1],
                time_end=row[2],
                sensor_type=row[3],
                sensor_name=row[4],
                robot_id=row[5],
                file_id=row[6],
                bucket=row[7],
                altitude=row[8],
                heading=row[9],
                pitch=row[10],
                roll=row[11],
                lon=row[12],
                lat=row[13],
            )
            for row in rows
        ]

    async def query_spatial(self, query: SpatialQuery) -> list[StoredRecord]:
        """Async wrapper for spatial bounding-box queries."""
        return await asyncio.to_thread(self._query_spatial_sync, query)
