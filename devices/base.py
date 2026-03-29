from __future__ import annotations

import asyncio
import time
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Optional

from bleak import BleakClient

from config_loader import DeviceConfig, Config
from signalk_sender import SignalKTcpServer

# Forward-declared to avoid a circular import at runtime.
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from adapter_restart import AdapterRestartCoordinator

_MAX_BACKOFF_SECONDS = 30


class DeviceState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RUNNING = "running"
    BACKOFF = "backoff"
    STOPPED = "stopped"


class BaseBleDevice(ABC):
    def __init__(self, app_config: Config, device_config: DeviceConfig):
        self.app_config = app_config
        self.config = device_config

        self.client: Optional[BleakClient] = None
        self.state: DeviceState = DeviceState.DISCONNECTED

        self.fail_count = 0
        self.last_data_time = 0.0
        self._stop_event = asyncio.Event()
        self._disconnected_event = asyncio.Event()

        self.signalk: Optional[SignalKTcpServer] = None
        self._pending_signalk: Optional[List[dict]] = None
        self.ble_connect_lock: Optional[asyncio.Lock] = None
        self.adapter_restart: Optional["AdapterRestartCoordinator"] = None

        self._logger = logging.getLogger(device_config.key)

    def log(self, message: str, level: int = logging.INFO) -> None:
        self._logger.log(level, message)


    def _queue_signalk(self, values: List[dict]) -> None:
        """Called by subclasses after parsing data to stage Signal K updates."""
        if values:
            self._pending_signalk = values

    async def _flush_signalk(self) -> None:
        """Send any pending Signal K values and clear the buffer."""
        if self.signalk is not None and self._pending_signalk:
            await self.signalk.send(self._pending_signalk)
            self._pending_signalk = None

    def mark_data_received(self) -> None:
        self.last_data_time = time.time()

    def seconds_since_last_data(self) -> float:
        if self.last_data_time <= 0:
            return 999999.0
        return time.time() - self.last_data_time

    def _on_ble_disconnect(self, client: BleakClient) -> None:
        """Bleak disconnection callback — signals the run loop to reconnect immediately."""
        if not self._stop_event.is_set():
            self._logger.info("Device disconnected (BLE callback)")
            self._disconnected_event.set()

    async def connect(self) -> None:
        # Clean up any stale client before attempting a new connection.
        # Without this, a crashed BleakClient object stays alive and BlueZ
        # may still consider the device connected, causing the next attempt
        # to fail immediately.
        if self.client is not None:
            try:
                await asyncio.wait_for(self.client.disconnect(), timeout=3.0)
            except Exception:
                pass
            self.client = None

        self.state = DeviceState.CONNECTING
        self._logger.info(f"Connecting to {self.config.mac} ...")

        if self.ble_connect_lock is not None:
            async with self.ble_connect_lock:
                self.client = BleakClient(
                    self.config.mac,
                    timeout=20.0,
                    disconnected_callback=self._on_ble_disconnect
                )
                await self.client.connect()
        else:
            self.client = BleakClient(
                self.config.mac,
                timeout=20.0,
                disconnected_callback=self._on_ble_disconnect
            )
            await self.client.connect()

        self.state = DeviceState.CONNECTED
        self._logger.info("Connected.")

    async def disconnect(self) -> None:
        if self.client is not None:
            try:
                if self.client.is_connected:
                    await asyncio.wait_for(self.client.disconnect(), timeout=5.0)
                    self._logger.info("Disconnected.")
            except Exception as e:
                self._logger.warning(f"Disconnect error: {e}")

        self.client = None
        self.state = DeviceState.DISCONNECTED

    async def start_notifications(self) -> None:
        if self.client is None:
            raise RuntimeError("Client is not connected")

        for uuid in self.config.notify_uuids:
            self._logger.info(f"Starting notify on {uuid}")
            handler = self._make_notify_handler(uuid)
            await self.client.start_notify(uuid, handler)

    def _make_notify_handler(self, uuid: str):
        """Return a notification callback, optionally wrapped with debug logging."""
        if not getattr(self.app_config.app, "enable_debug_log", False):
            return self.notification_handler

        short = uuid[-8:]

        def debug_handler(characteristic, data: bytearray) -> None:
            self._logger.debug(f"[DEBUG:{short}] {data.hex()}")
            self.notification_handler(characteristic, data)

        return debug_handler

    async def stop_notifications(self) -> None:
        if self.client is None:
            return

        for uuid in self.config.notify_uuids:
            try:
                await self.client.stop_notify(uuid)
            except Exception:
                pass

    async def run(self) -> None:
        while not self._stop_event.is_set():
            self._disconnected_event.clear()
            try:
                await self.connect()
                await self.start_notifications()
                await self.on_after_connect()

                self.fail_count = 0
                self.mark_data_received()
                self.state = DeviceState.RUNNING

                self._logger.info("Running.")
                await self.run_connected_loop()

            except asyncio.CancelledError:
                self._logger.warning("Cancelled.")
                break

            except Exception as e:
                self.fail_count += 1
                self._logger.error(f"Error (fail #{self.fail_count}): {e}")

                # If adapter restart is configured and we've hit the threshold,
                # ask the coordinator to restart the Bluetooth adapter.  The
                # coordinator serialises restarts and enforces a cooldown, so
                # it's safe to call from every device independently.
                if (
                    self.adapter_restart is not None
                    and self.fail_count >= self.config.max_fail_before_restart
                ):
                    restarted = await self.adapter_restart.maybe_restart(self)
                    if restarted:
                        # Reset so the backoff starts fresh after the restart.
                        self.fail_count = 0

            finally:
                try:
                    await self.on_before_disconnect()
                except Exception as e:
                    self._logger.warning(f"on_before_disconnect error: {e}")

                try:
                    await self.stop_notifications()
                except Exception:
                    pass

                await self.disconnect()

            if self._stop_event.is_set():
                break

            # Exponential backoff: base * 2^(fail-1), capped at _MAX_BACKOFF_SECONDS.
            # backoff = min(
            #     self.config.reconnect_delay_seconds * (1.3 ** (self.fail_count - 1)),
            #     _MAX_BACKOFF_SECONDS,
            #     )

            self.state = DeviceState.BACKOFF
            self._logger.info(f"Backing off for {self.config.reconnect_delay_seconds}s ...")
            await asyncio.sleep(self.config.reconnect_delay_seconds)
            #
            # self._logger.info(
            #     f"Backing off {backoff:.0f}s (fail #{self.fail_count}) ..."
            # )
            # await asyncio.sleep(backoff)



        self.state = DeviceState.STOPPED
        self._logger.info("Stopped.")

    async def stop(self) -> None:
        self._stop_event.set()
        await self.disconnect()

    async def run_connected_loop(self) -> None:
        if self.client is None:
            raise RuntimeError("Client is not connected")

        while not self._stop_event.is_set():
            await asyncio.sleep(1.0)

            # Check the disconnect callback event first (faster than polling is_connected).
            if self._disconnected_event.is_set():
                raise ConnectionError("Device disconnected")

            if not self.client.is_connected:
                raise ConnectionError("Device disconnected")

            age = self.seconds_since_last_data()
            if age > self.config.watchdog_timeout_seconds:
                raise TimeoutError(
                    f"Watchdog timeout: no data for {age:.1f}s "
                    f"(limit: {self.config.watchdog_timeout_seconds}s)"
                )

            await self.on_tick()
            await self._flush_signalk()

    @abstractmethod
    def notification_handler(self, characteristic, data: bytearray) -> None:
        """
        子类处理 notify 数据。
        收到有效数据后应调用 self.mark_data_received()
        """
        raise NotImplementedError

    async def on_after_connect(self) -> None:
        """
        子类可选重写：
        - 连接后发送初始化命令
        - 建立额外状态
        """
        return

    async def on_before_disconnect(self) -> None:
        """
        子类可选重写：
        - 断开前清理状态
        """
        return

    async def on_tick(self) -> None:
        """
        子类可选重写：
        - 每秒轮询一次
        - MPPT 可以在这里发读命令
        """
        return