"""Integration smoke test for the Robot Manager pipeline.

Starts all services with a minimal 2-sensor config (one frame, one video),
lets data accumulate, issues a Zenoh spatial query, and validates end-to-end
correctness of ingest, storage, and query.
"""

from __future__ import annotations

import asyncio
import json
import logging
import resource
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import zenoh
from reduct import Client as ReductClient

from core.bus import EventBus
from core.config import RobotConfig, SensorConfig
from core.models import SpatialQuery
from database.db_client import DBClient
from services.network_worker import NetworkWorker
from services.sensor_worker import SensorWorker
from services.storage_worker import StorageWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

REDUCTSTORE_URL = "http://127.0.0.1:8383"
ACCUMULATION_SECONDS = 5
ZENOH_LISTEN_ENDPOINT = "tcp/127.0.0.1:7447"

# Prague center — mock sensors jitter around 50.08°N, 14.42°E (±0.001)
QUERY_BBOX = (14.41, 50.07, 14.43, 50.09)  # (lon_min, lat_min, lon_max, lat_max)


def _make_test_config() -> RobotConfig:
    """Build a minimal config with one frame sensor and one video sensor."""
    return RobotConfig(
        robot_id="test_robot_01",
        robot_type="uav",
        sensors=[
            SensorConfig(
                name="test_rgb",
                type="rgb_photo",
                driver="MockFrameSensor",
                params={"resolution": [640, 480], "fps": 2},
                reductstore_bucket="test_rgb_photo",
            ),
            SensorConfig(
                name="test_video",
                type="rgb_video",
                driver="MockVideoSensor",
                params={
                    "resolution": [640, 480],
                    "fps": 30,
                    "chunk_duration_sec": 1,
                },
                reductstore_bucket="test_rgb_video",
            ),
        ],
    )


@pytest.fixture()
def test_config() -> RobotConfig:
    return _make_test_config()


@pytest.fixture()
def db_client(tmp_path: Path) -> DBClient:
    db_path = tmp_path / "test_robot.gpkg"
    client = DBClient(db_path)
    client.connect()
    yield client
    client.close()


@pytest.fixture()
def reduct_client() -> ReductClient:
    return ReductClient(REDUCTSTORE_URL)


@pytest.mark.asyncio
async def test_full_pipeline(
    test_config: RobotConfig,
    db_client: DBClient,
    reduct_client: ReductClient,
) -> None:
    """End-to-end smoke test: ingest → store → query → verify."""
    bus = EventBus()
    start_time = datetime.now(timezone.utc)

    # --- Start all services ---
    sensor_worker = SensorWorker(bus, test_config)
    storage_worker = StorageWorker(
        bus, db_client, test_config, reduct_client, bucket_quota_bytes=1 * 1024**3
    )
    network_worker = NetworkWorker(
        db_client,
        test_config,
        reduct_client,
        listen_endpoints=[ZENOH_LISTEN_ENDPOINT],
    )

    await sensor_worker.start()
    await storage_worker.start()
    await network_worker.start()

    # Open Zenoh client session after NetworkWorker is listening
    client_cfg = zenoh.Config()
    client_cfg.insert_json5("mode", '"peer"')
    client_cfg.insert_json5(
        "connect/endpoints", json.dumps([ZENOH_LISTEN_ENDPOINT])
    )
    client_session = zenoh.open(client_cfg)

    logger.info(
        "All services started — accumulating data for %ds", ACCUMULATION_SECONDS
    )

    try:
        # --- Wait for data to accumulate and peers to connect ---
        await asyncio.sleep(ACCUMULATION_SECONDS)

        # --- Send a Zenoh spatial query ---
        query_payload = {
            "bbox": list(QUERY_BBOX),
            "time_start": (start_time - timedelta(minutes=1)).isoformat(),
            "time_end": (
                datetime.now(timezone.utc) + timedelta(minutes=1)
            ).isoformat(),
        }
        payload_bytes = json.dumps(query_payload).encode()

        def _do_zenoh_query() -> tuple[list[dict], float]:
            """Run the blocking Zenoh get in a thread to keep the event loop free."""
            t0 = time.monotonic()
            handler = client_session.get(
                "robot/test_robot_01/query/spatial",
                payload=zenoh.ZBytes(payload_bytes),
                timeout=5.0,
            )
            res: list[dict] = []
            for reply in handler:
                if reply.ok is not None:
                    res.append(json.loads(reply.ok.payload.to_bytes()))
            return res, (time.monotonic() - t0) * 1000

        results, query_latency_ms = await asyncio.to_thread(_do_zenoh_query)

    finally:
        client_session.close()
        # --- Shut down services cleanly (always, even on failure) ---
        await network_worker.stop()
        await storage_worker.stop()
        await sensor_worker.stop()

    # --- Verify Zenoh query results ---
    assert len(results) >= 1, "Expected at least one Zenoh reply"
    response = results[0]
    assert response["robot_id"] == "test_robot_01"
    assert len(response["results"]) > 0, "Expected query results with matching data"

    for entry in response["results"]:
        meta = entry["metadata"]
        assert meta["robot_id"] == "test_robot_01"
        assert meta["sensor_type"] in ("rgb_photo", "rgb_video")
        assert -180.0 <= meta["lon"] <= 180.0
        assert -90.0 <= meta["lat"] <= 90.0
        assert entry["data_b64"], "Expected non-empty base64 blob data"

    # --- Verify SpatiaLite records ---
    spatial_query = SpatialQuery(
        bbox=QUERY_BBOX,
        time_start=start_time - timedelta(minutes=1),
        time_end=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    db_records = await db_client.query_spatial(spatial_query)
    assert len(db_records) > 0, "Expected records in SpatiaLite"

    for rec in db_records:
        assert -180.0 <= rec.lon <= 180.0, f"Invalid lon: {rec.lon}"
        assert -90.0 <= rec.lat <= 90.0, f"Invalid lat: {rec.lat}"

    sensor_types_found = {rec.sensor_type for rec in db_records}
    assert "rgb_photo" in sensor_types_found, "Expected rgb_photo records in DB"
    assert "rgb_video" in sensor_types_found, "Expected rgb_video records in DB"

    # --- Verify ReductStore buckets contain blobs ---
    for bucket_name in ("test_rgb_photo", "test_rgb_video"):
        bucket = await reduct_client.get_bucket(bucket_name)
        info = await bucket.get_full_info()
        assert info.info.size > 0, f"Bucket '{bucket_name}' should contain data"

    # --- Print summary ---
    mem_usage_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    total_records = len(db_records)
    frame_records = sum(1 for r in db_records if r.sensor_type == "rgb_photo")
    video_records = sum(1 for r in db_records if r.sensor_type == "rgb_video")

    print("\n" + "=" * 60)
    print("INTEGRATION SMOKE TEST — SUMMARY")
    print("=" * 60)
    print(f"  Total records ingested:   {total_records}")
    print(f"    Frame sensor (rgb):     {frame_records}")
    print(f"    Video sensor (video):   {video_records}")
    print(f"  Zenoh query latency:      {query_latency_ms:.1f} ms")
    print(f"  Query results returned:   {len(response['results'])}")
    print(f"  Peak memory (RSS):        {mem_usage_mb:.1f} MB")
    print("=" * 60)

    # --- Cleanup ReductStore test buckets ---
    for bucket_name in ("test_rgb_photo", "test_rgb_video"):
        try:
            bucket = await reduct_client.get_bucket(bucket_name)
            await bucket.remove()
        except Exception:
            pass
