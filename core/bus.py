from __future__ import annotations

import asyncio

from core.models import SensorSample


class EventBus:
    """Central message bus connecting producers to consumers via async queues."""

    def __init__(self) -> None:
        self.storage_queue: asyncio.Queue[SensorSample] = asyncio.Queue()
