from __future__ import annotations

import asyncio
import logging
import signal

from reduct import Client as ReductClient

from core.bus import EventBus
from core.config import load_config
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


async def main() -> None:
    """Start the Robot Manager and all services."""
    config = load_config("robot_config.yaml")

    bus = EventBus()

    db_client = DBClient()
    db_client.connect()

    reduct_client = ReductClient(REDUCTSTORE_URL)

    sensor_worker = SensorWorker(bus, config)
    storage_worker = StorageWorker(bus, db_client, config, reduct_client)
    network_worker = NetworkWorker(db_client, config, reduct_client)

    services = [sensor_worker, storage_worker, network_worker]

    buckets = {s.reductstore_bucket for s in config.sensors}
    logger.info(
        "Starting Robot Manager — robot_id=%s, robot_type=%s, sensors=%d, buckets=%s",
        config.robot_id,
        config.robot_type,
        len(config.sensors),
        sorted(buckets),
    )

    for service in services:
        await service.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()

    logger.info("Shutting down…")
    for service in reversed(services):
        await service.stop()

    db_client.close()
    logger.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
