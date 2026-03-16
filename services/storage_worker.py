from __future__ import annotations

import asyncio
import logging
import time

from reduct import Client as ReductClient, BucketSettings, QuotaType

from core.base_service import BaseService
from core.bus import EventBus
from core.config import RobotConfig
from core.models import SensorSample
from database.db_client import DBClient

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 50
FLUSH_INTERVAL_SEC = 0.5
DEFAULT_BUCKET_QUOTA_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB


class StorageWorker(BaseService):
    """Consumes SensorSamples from the event bus, batches writes to ReductStore and SpatiaLite."""

    def __init__(
        self,
        bus: EventBus,
        db_client: DBClient,
        config: RobotConfig,
        reduct_client: ReductClient,
        bucket_quota_bytes: int = DEFAULT_BUCKET_QUOTA_BYTES,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._db = db_client
        self._config = config
        self._reduct = reduct_client
        self._bucket_quota_bytes = bucket_quota_bytes
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Initialise ReductStore buckets with FIFO quotas and start the consumer loop."""
        await self._ensure_buckets()
        self.is_running = True
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("StorageWorker started")

    async def stop(self) -> None:
        """Drain remaining buffer and shut down."""
        self.is_running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("StorageWorker stopped")

    async def _ensure_buckets(self) -> None:
        """Create ReductStore buckets with FIFO quota if they don't already exist."""
        seen: set[str] = set()
        for sensor in self._config.sensors:
            bucket_name = sensor.reductstore_bucket
            if bucket_name in seen:
                continue
            seen.add(bucket_name)

            settings = BucketSettings(
                quota_type=QuotaType.FIFO,
                quota_size=self._bucket_quota_bytes,
            )
            await self._reduct.create_bucket(bucket_name, settings, exist_ok=True)
            logger.info(
                "Ensured bucket '%s' with FIFO quota %d bytes",
                bucket_name,
                self._bucket_quota_bytes,
            )

    async def _consume_loop(self) -> None:
        """Time-or-count batching consumer loop."""
        batch: list[SensorSample] = []
        last_flush = time.monotonic()

        while self.is_running:
            # Calculate how long until the next time-based flush
            elapsed = time.monotonic() - last_flush
            remaining = max(0.0, FLUSH_INTERVAL_SEC - elapsed)

            try:
                sample = await asyncio.wait_for(
                    self._bus.storage_queue.get(), timeout=remaining
                )
                batch.append(sample)
                self._bus.storage_queue.task_done()
            except asyncio.TimeoutError:
                # Flush interval elapsed — fall through to flush check
                pass

            # Flush when batch is full or the time interval has elapsed
            should_flush = (
                len(batch) >= MAX_BATCH_SIZE
                or time.monotonic() - last_flush >= FLUSH_INTERVAL_SEC
            )
            if should_flush and batch:
                await self._flush(batch)
                batch = []
                last_flush = time.monotonic()

        # Drain any remaining items on shutdown
        if batch:
            await self._flush(batch)

    async def _flush(self, batch: list[SensorSample]) -> None:
        """Write a batch of samples to ReductStore and SpatiaLite."""
        try:
            # Write blobs to ReductStore
            for sample in batch:
                bucket = await self._reduct.get_bucket(sample.bucket)
                ts_us = int(sample.timestamp.timestamp() * 1_000_000)
                await bucket.write(
                    sample.sensor_name,
                    sample.data,
                    timestamp=ts_us,
                )

            # Write metadata to SpatiaLite
            await self._db.insert_batch(batch, self._config.robot_id)
            logger.debug("Flushed batch of %d samples", len(batch))
        except Exception:
            logger.exception("Error flushing batch of %d samples", len(batch))
