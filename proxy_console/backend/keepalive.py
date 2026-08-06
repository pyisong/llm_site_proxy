"""Background keepalive scheduler.

Checks periodically; only sends a real chat probe when a proxy has had
no request traffic for CONSOLE_KEEPALIVE_IDLE_SEC (default 2 days).
"""

from __future__ import annotations

import asyncio
import logging
import os

from catalog_client import keepalive_tick

log = logging.getLogger("proxy_console.keepalive")


class KeepaliveRunner:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    @property
    def interval(self) -> float:
        """How often to *check* idle status (not how often to chat)."""
        try:
            # Default 1h; clamp to >= 5min so we don't spin
            return max(300.0, float(os.getenv("CONSOLE_KEEPALIVE_CHECK_INTERVAL", "3600")))
        except ValueError:
            return 3600.0

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="keepalive")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        # Initial delay so app boots before first check
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=15.0)
            return
        except asyncio.TimeoutError:
            pass
        while not self._stop.is_set():
            try:
                results = await keepalive_tick()
                if results:
                    fails = [r for r in results if not r.get("ok")]
                    log.info(
                        "keepalive probed ok=%s fail=%s",
                        len(results) - len(fails),
                        len(fails),
                    )
                else:
                    log.info("keepalive check: all proxies have recent traffic, skipped")
            except Exception:  # noqa: BLE001
                log.exception("keepalive tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
                break
            except asyncio.TimeoutError:
                continue


runner = KeepaliveRunner()
