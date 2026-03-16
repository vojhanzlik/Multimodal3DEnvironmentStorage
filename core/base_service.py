from __future__ import annotations

import abc


class BaseService(abc.ABC):
    """Common interface for all long-running system services."""

    def __init__(self) -> None:
        self.is_running: bool = False

    @abc.abstractmethod
    async def start(self) -> None:
        """Start the service and begin processing."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down the service."""
