from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Optional

from bleak import BleakClient

from config_loader import DeviceConfig, Config
from signalk_sender import SignalKTcpServer


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

        self.signalk: Optional[SignalKTcpServer] = None
        self._pending_signalk: Optional[List[dict]] = None
        self.ble_connect_lock: Optional[asyncio.Lock] = None

    def log(self, message: str) -> None:
        print(f"[{self.config.key}] {message}")

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
        self.log(f"Connecting to {self.config.mac} ...")

        if self.ble_connect_lock is not None:
            async with self.ble_connect_lock:
                self.client = BleakClient(self.config.mac, timeout=20.0)
                await self.client.connect()
        else:
            self.client = BleakClient(self.config.mac, timeout=20.0)
            await self.client.connect()

        self.state = DeviceState.CONNECTED
        self.log("Connected.")

    async def disconnect(self) -> None:
        if self.client is not None:
            try:
                if self.client.is_connected:
                    await asyncio.wait_for(self.client.disconnect(), timeout=5.0)
                    self.log("Disconnected.")
            except Exception as e:
                self.log(f"Disconnect error: {e}")

        self.client = None
        self.state = DeviceState.DISCONNECTED

    async def start_notifications(self) -> None:
        if self.client is None:
            raise RuntimeError("Client is not connected")

        for uuid in self.config.notify_uuids:
            self.log(f"Starting notify on {uuid}")
            await self.client.start_notify(uuid, self.notification_handler)

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
            try:
                await self.connect()
                await self.start_notifications()
                await self.on_after_connect()

                self.fail_count = 0
                self.mark_data_received()
                self.state = DeviceState.RUNNING

                self.log("Running.")
                await self.run_connected_loop()

            except asyncio.CancelledError:
                self.log("Cancelled.")
                break

            except Exception as e:
                self.fail_count += 1
                self.log(f"Error (fail #{self.fail_count}): {e}")

            finally:
                try:
                    await self.on_before_disconnect()
                except Exception as e:
                    self.log(f"on_before_disconnect error: {e}")

                try:
                    await self.stop_notifications()
                except Exception:
                    pass

                await self.disconnect()

            if self._stop_event.is_set():
                break

            self.state = DeviceState.BACKOFF
            self.log(f"Backing off for {self.config.reconnect_delay_seconds}s ...")
            await asyncio.sleep(self.config.reconnect_delay_seconds)

        self.state = DeviceState.STOPPED
        self.log("Stopped.")

    async def stop(self) -> None:
        self._stop_event.set()
        await self.disconnect()

    async def run_connected_loop(self) -> None:
        if self.client is None:
            raise RuntimeError("Client is not connected")

        while self.client.is_connected and not self._stop_event.is_set():
            await asyncio.sleep(1)

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