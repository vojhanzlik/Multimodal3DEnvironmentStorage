from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone

import zenoh
from reduct import Client as ReductClient

from core.base_service import BaseService
from core.config import RobotConfig
from core.models import QueryResult, SpatialQuery, StoredRecord
from database.db_client import DBClient

logger = logging.getLogger(__name__)

QUERY_KEY_EXPR = "robot/*/query/spatial"


class NetworkWorker(BaseService):
    """Handles distributed spatial queries via Zenoh broadcast scatter-gather."""

    def __init__(
        self,
        db_client: DBClient,
        config: RobotConfig,
        reduct_client: ReductClient,
        listen_endpoints: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._db = db_client
        self._config = config
        self._reduct = reduct_client
        self._listen_endpoints = listen_endpoints
        self._session: zenoh.Session | None = None
        self._queryable: zenoh.Queryable | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        """Open a Zenoh peer session and register the spatial queryable."""
        self._loop = asyncio.get_running_loop()
        cfg = zenoh.Config()
        cfg.insert_json5("mode", '"peer"')
        if self._listen_endpoints:
            cfg.insert_json5("listen/endpoints", json.dumps(self._listen_endpoints))

        self._session = zenoh.open(cfg)
        self._queryable = self._session.declare_queryable(
            QUERY_KEY_EXPR, self._on_query
        )
        self.is_running = True
        logger.info(
            "NetworkWorker started — queryable on '%s' (robot_id=%s)",
            QUERY_KEY_EXPR,
            self._config.robot_id,
        )

    async def stop(self) -> None:
        """Shut down the queryable, close the Zenoh session."""
        self.is_running = False
        if self._queryable is not None:
            self._session.undeclare(self._queryable)
            self._queryable = None
        if self._session is not None:
            self._session.close()
            self._session = None
        self._loop = None
        logger.info("NetworkWorker stopped")

    def _on_query(self, query: zenoh.Query) -> None:
        """Zenoh callback — schedule async handling on the event loop."""
        if self._loop is not None and self.is_running:
            self._loop.call_soon_threadsafe(
                asyncio.ensure_future, self._handle_query(query)
            )

    async def _handle_query(self, query: zenoh.Query) -> None:
        """Parse a spatial query, run it against SpatiaLite, and reply."""
        try:
            payload = query.payload
            if payload is None:
                query.reply_err(zenoh.ZBytes(b"missing payload"))
                return

            raw = json.loads(payload.to_bytes())
            spatial_query = SpatialQuery(
                bbox=tuple(raw["bbox"]),
                time_start=datetime.fromisoformat(raw["time_start"]).replace(
                    tzinfo=timezone.utc
                ),
                time_end=datetime.fromisoformat(raw["time_end"]).replace(
                    tzinfo=timezone.utc
                ),
                sensor_type=raw.get("sensor_type"),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("Malformed query payload: %s", exc)
            query.reply_err(zenoh.ZBytes(f"bad request: {exc}".encode()))
            return

        records = await self._db.query_spatial(spatial_query)

        results: list[dict] = []
        for record in records:
            data_b64 = await self._fetch_blob_b64(record)
            result = QueryResult(metadata=record, data_b64=data_b64)
            results.append(_query_result_to_dict(result))

        response = json.dumps(
            {"robot_id": self._config.robot_id, "results": results}
        ).encode()
        query.reply(query.key_expr, zenoh.ZBytes(response))

    async def _fetch_blob_b64(self, record: StoredRecord) -> str:
        """Fetch a binary blob from ReductStore and return it as base64."""
        try:
            bucket = await self._reduct.get_bucket(record.bucket)
            ts = _ensure_datetime(record.timestamp)
            ts_us = int(ts.timestamp() * 1_000_000)
            async with bucket.read(record.sensor_name, timestamp=ts_us) as entry:
                data = await entry.read_all()
            return base64.b64encode(data).decode("ascii")
        except Exception:
            logger.exception(
                "Failed to fetch blob for record %d from bucket '%s'",
                record.id,
                record.bucket,
            )
            return ""


def _ensure_datetime(val: datetime | str) -> datetime:
    """Convert an ISO string to datetime if needed (SQLite returns strings)."""
    if isinstance(val, str):
        return datetime.fromisoformat(val).replace(tzinfo=timezone.utc)
    return val


def _query_result_to_dict(result: QueryResult) -> dict:
    """Serialize a QueryResult to a JSON-compatible dict."""
    meta = asdict(result.metadata)
    for key in ("timestamp", "time_end"):
        val = meta[key]
        if isinstance(val, datetime):
            meta[key] = val.isoformat()
        # Already a string from SQLite — leave as-is
    return {"metadata": meta, "data_b64": result.data_b64}
