from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Union

import cpp_hal
from core.base_service import BaseService
from core.bus import EventBus
from core.config import RobotConfig, SensorConfig
from core.models import SensorSample

logger = logging.getLogger(__name__)

VIDEO_SENSOR_TYPES: set[str] = {"rgb_video"}

# Default mock data sizes (bytes) when resolution is not provided.
DEFAULT_FRAME_SIZE_BYTES = 2_000_000
DEFAULT_VIDEO_CHUNK_SIZE_BYTES = 15_000_000


def _estimate_frame_size(params: dict) -> int:
    """Estimate raw frame size from resolution, or fall back to a default."""
    resolution = params.get("resolution")
    if resolution and len(resolution) == 2:
        # ~3 bytes per pixel (RGB) as a rough estimate
        return resolution[0] * resolution[1] * 3
    return DEFAULT_FRAME_SIZE_BYTES


def _estimate_chunk_size(params: dict) -> int:
    """Estimate video chunk size from resolution, fps, and duration."""
    resolution = params.get("resolution")
    fps = params.get("fps", 30)
    duration = params.get("chunk_duration_sec", 2)
    if resolution and len(resolution) == 2:
        # Rough H.265 estimate: ~0.5 bytes per pixel per frame
        return int(resolution[0] * resolution[1] * 0.5 * fps * duration)
    return DEFAULT_VIDEO_CHUNK_SIZE_BYTES


def _create_sensor(
    cfg: SensorConfig,
) -> Union[cpp_hal.IFrameSensor, cpp_hal.IVideoSensor]:
    """Instantiate a C++ HAL sensor from a config entry."""
    if cfg.type in VIDEO_SENSOR_TYPES:
        chunk_size = _estimate_chunk_size(cfg.params)
        chunk_duration = cfg.params.get("chunk_duration_sec", 2)
        return cpp_hal.create_video_sensor(
            name=cfg.name,
            type=cfg.type,
            chunk_size_bytes=chunk_size,
            chunk_duration_sec=chunk_duration,
        )
    interval_ms = int(1000 / cfg.params.get("fps", 1))
    data_size = _estimate_frame_size(cfg.params)
    return cpp_hal.create_frame_sensor(
        name=cfg.name,
        type=cfg.type,
        data_size_bytes=data_size,
        interval_ms=interval_ms,
    )


class SensorWorker(BaseService):
    """Spawns one async producer task per configured sensor."""

    def __init__(self, bus: EventBus, config: RobotConfig) -> None:
        super().__init__()
        self._bus = bus
        self._config = config
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Create C++ sensors from config and launch producer tasks."""
        self.is_running = True
        for sensor_cfg in self._config.sensors:
            sensor = _create_sensor(sensor_cfg)
            if sensor_cfg.type in VIDEO_SENSOR_TYPES:
                task = asyncio.create_task(
                    self._video_loop(sensor, sensor_cfg),
                    name=f"sensor-{sensor_cfg.name}",
                )
            else:
                task = asyncio.create_task(
                    self._frame_loop(sensor, sensor_cfg),
                    name=f"sensor-{sensor_cfg.name}",
                )
            self._tasks.append(task)
            logger.info("Started sensor task: %s (%s)", sensor_cfg.name, sensor_cfg.type)

    async def stop(self) -> None:
        """Cancel all sensor tasks and wait for them to finish."""
        self.is_running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("All sensor tasks stopped")

    async def _frame_loop(
        self, sensor: cpp_hal.IFrameSensor, cfg: SensorConfig
    ) -> None:
        """Continuously poll a discrete-frame sensor and enqueue samples."""
        while self.is_running:
            try:
                frame = await asyncio.to_thread(sensor.get_latest_frame)
                sample = SensorSample(
                    data=bytes(frame.data),
                    timestamp=datetime.now(timezone.utc),
                    time_end=None,
                    sensor_type=frame.sensor_type,
                    sensor_name=frame.sensor_name,
                    lat=frame.latitude,
                    lon=frame.longitude,
                    altitude=frame.altitude,
                    bucket=cfg.reductstore_bucket,
                )
                await self._bus.storage_queue.put(sample)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in frame sensor %s", cfg.name)

    async def _video_loop(
        self, sensor: cpp_hal.IVideoSensor, cfg: SensorConfig
    ) -> None:
        """Continuously poll a video-chunk sensor and enqueue samples."""
        while self.is_running:
            try:
                chunk = await asyncio.to_thread(sensor.get_latest_chunk)
                sample = SensorSample(
                    data=bytes(chunk.data),
                    timestamp=datetime.fromtimestamp(chunk.time_start, tz=timezone.utc),
                    time_end=datetime.fromtimestamp(chunk.time_end, tz=timezone.utc),
                    sensor_type=chunk.sensor_type,
                    sensor_name=chunk.sensor_name,
                    lat=chunk.latitude,
                    lon=chunk.longitude,
                    altitude=chunk.altitude,
                    bucket=cfg.reductstore_bucket,
                )
                await self._bus.storage_queue.put(sample)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in video sensor %s", cfg.name)
