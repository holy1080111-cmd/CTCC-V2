from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import json
import logging
from typing import Awaitable, Callable

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.config.settings import Settings
from app.domain.realtime import RealtimeStatus
from app.exchange.okx.ws_parser import OkxWsParseError, parse_public_message

logger = logging.getLogger(__name__)
EventHandler = Callable[[dict], Awaitable[object]]


class OkxPublicWebSocket:
    """Resilient OKX public WebSocket consumer.

    It uses public channels only and never authenticates or submits orders.
    """

    CHANNELS = ("tickers", "mark-price", "funding-rate", "open-interest", "trades", "books5")

    def __init__(self, settings: Settings, handler: EventHandler) -> None:
        self.settings = settings
        self.handler = handler
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._connected = False
        self._connection_count = 0
        self._reconnect_count = 0
        self._message_count = 0
        self._parse_error_count = 0
        self._last_connected_at: datetime | None = None
        self._last_message_at: datetime | None = None
        self._last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def subscription_args(self) -> list[dict[str, str]]:
        return [
            {"channel": channel, "instId": symbol}
            for symbol in self.settings.okx_ws_symbol_list
            for channel in self.CHANNELS
        ]

    def status(self) -> RealtimeStatus:
        return RealtimeStatus(
            enabled=self.settings.okx_ws_enabled,
            running=self.running,
            connected=self._connected,
            endpoint=self.settings.okx_ws_public_url,
            symbols=self.settings.okx_ws_symbol_list,
            connection_count=self._connection_count,
            reconnect_count=self._reconnect_count,
            message_count=self._message_count,
            parse_error_count=self._parse_error_count,
            last_connected_at=self._last_connected_at,
            last_message_at=self._last_message_at,
            last_error=self._last_error,
            paper_auto_ticks=self.settings.paper_auto_ticks,
        )

    async def start(self) -> None:
        if self.running:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="okx-public-websocket")

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._connected = False

    async def _run(self) -> None:
        delay = self.settings.okx_ws_reconnect_initial_seconds
        first_attempt = True
        while not self._stop.is_set():
            try:
                async with asyncio.timeout(self.settings.okx_ws_connect_timeout_seconds):
                    websocket_context = connect(
                        self.settings.okx_ws_public_url,
                        ping_interval=20,
                        ping_timeout=self.settings.okx_ws_ping_timeout_seconds,
                        close_timeout=5,
                        max_size=self.settings.okx_ws_max_message_size,
                    )
                    websocket = await websocket_context.__aenter__()
                try:
                    self._connected = True
                    self._connection_count += 1
                    if not first_attempt:
                        self._reconnect_count += 1
                    first_attempt = False
                    self._last_connected_at = datetime.now(timezone.utc)
                    self._last_error = None
                    delay = self.settings.okx_ws_reconnect_initial_seconds
                    await websocket.send(json.dumps({"op": "subscribe", "args": self.subscription_args()}))
                    await self._consume(websocket)
                finally:
                    self._connected = False
                    await websocket_context.__aexit__(None, None, None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected = False
                self._last_error = f"{exc.__class__.__name__}: {exc}"
                logger.warning("okx_ws_disconnected error=%s retry_in=%s", self._last_error, delay)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except TimeoutError:
                    pass
                delay = min(delay * 2, self.settings.okx_ws_reconnect_max_seconds)

    async def _consume(self, websocket) -> None:
        while not self._stop.is_set():
            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=self.settings.okx_ws_receive_timeout_seconds)
            except TimeoutError:
                await websocket.send("ping")
                raw = await asyncio.wait_for(websocket.recv(), timeout=self.settings.okx_ws_ping_timeout_seconds)
            except ConnectionClosed:
                return

            if raw == "pong":
                continue
            self._last_message_at = datetime.now(timezone.utc)
            self._message_count += 1
            try:
                payload = json.loads(raw)
                for event in parse_public_message(payload):
                    await self.handler(event)
            except (json.JSONDecodeError, OkxWsParseError, TypeError, ValueError) as exc:
                self._parse_error_count += 1
                self._last_error = f"{exc.__class__.__name__}: {exc}"
                logger.exception("okx_ws_parse_error raw=%r", raw[:1000])
