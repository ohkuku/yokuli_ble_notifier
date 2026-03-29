from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from devices.base import BaseBleDevice

from config_loader import BluetoothConfig

_log = logging.getLogger("adapter")


async def _btctl(*args: str, timeout: float = 6.0) -> None:
    """Run a bluetoothctl sub-command, ignoring errors."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl", *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except Exception as exc:
        _log.warning(f"bluetoothctl {' '.join(args)} failed: {exc}")


class AdapterRestartCoordinator:
    """
    Coordinates a global Bluetooth adapter restart across all BLE devices.

    Any device with adapter_restart_on_fail=true will call maybe_restart()
    when its consecutive failure count reaches max_fail_before_restart.
    Only one restart runs at a time (asyncio.Lock), and restart_cooldown_seconds
    prevents restart storms.

    Restart sequence
    ----------------
    1. Signal every device's _disconnected_event so its run-loop exits
       run_connected_loop immediately and BleakClient.disconnect() is called.
    2. Wait 2 s for Python-level disconnections to complete.
    3. For each device MAC:
         bluetoothctl disconnect <MAC>   — explicit system-level disconnect
         bluetoothctl remove <MAC>       — purge BlueZ device cache; this is
                                          the key step that clears stale
                                          connection state and forces a fresh
                                          scan+pair on the next connect attempt
    4. bluetoothctl power off            — power-cycle the adapter
    5. Wait 1 s
    6. bluetoothctl power on
    7. Wait adapter_settle_seconds (default 4 s) for the adapter to
       re-enumerate and be ready to accept new connections.

    No sudo or systemctl required — only bluetoothctl, which works for any
    user in the bluetooth group.
    """

    def __init__(
        self,
        bt_config: BluetoothConfig,
        all_devices: "List[BaseBleDevice]",
        adapter_settle_seconds: float = 4.0,
    ) -> None:
        self._config = bt_config
        self._all_devices = all_devices
        self._settle = adapter_settle_seconds
        self._lock = asyncio.Lock()
        self._last_restart_time: float = 0.0

    async def maybe_restart(self, requesting_device: "BaseBleDevice") -> bool:
        """
        Trigger a restart if enabled and the cooldown has elapsed.

        Returns True when a restart was performed (caller should reset
        fail_count).  Returns False when disabled, still in cooldown, or
        another device just ran one.
        """
        if not self._config.enable_adapter_restart:
            requesting_device._logger.info(
                "Adapter restart skipped — enable_adapter_restart is false"
            )
            return False

        now = time.time()
        remaining = self._config.restart_cooldown_seconds - (now - self._last_restart_time)
        if remaining > 0:
            requesting_device._logger.info(
                f"Adapter restart skipped — cooldown {remaining:.0f}s remaining"
            )
            return False

        async with self._lock:
            # Re-check: another device may have just restarted while we waited.
            now = time.time()
            remaining = self._config.restart_cooldown_seconds - (now - self._last_restart_time)
            if remaining > 0:
                requesting_device._logger.info(
                    "Adapter restart already performed by another device"
                )
                return False

            self._last_restart_time = time.time()
            await self._do_restart(requesting_device)
            return True

    async def force_restart(self, requesting_device: "BaseBleDevice") -> None:
        """
        Unconditional restart (used by the web dashboard's manual button).
        Bypasses the enable_adapter_restart flag and cooldown check, but
        updates _last_restart_time so the auto-restart cooldown is accurate.
        """
        async with self._lock:
            self._last_restart_time = time.time()
            await self._do_restart(requesting_device)

    async def _do_restart(self, requesting_device: "BaseBleDevice") -> None:
        logger = requesting_device._logger
        logger.warning("=== Bluetooth adapter restart triggered ===")

        # ── Step 1: signal all devices to exit their connected loop ──────────
        _log.info("Signalling all devices to disconnect ...")
        for device in self._all_devices:
            if not device._stop_event.is_set():
                device._disconnected_event.set()

        # Give run-loops time to call BleakClient.disconnect().
        await asyncio.sleep(2.0)

        # ── Step 2: per-device BlueZ cleanup ─────────────────────────────────
        for device in self._all_devices:
            mac = device.config.mac
            # Explicit disconnect first (belt-and-suspenders).
            _log.info(f"bluetoothctl disconnect {mac}")
            await _btctl("disconnect", mac)
            # Remove purges the device from BlueZ's cache so the next
            # connection goes through a clean scan+connect path rather than
            # trying to reuse stale state.
            _log.info(f"bluetoothctl remove {mac}")
            await _btctl("remove", mac)

        # ── Step 3: power-cycle the adapter ──────────────────────────────────
        _log.info("bluetoothctl power off")
        await _btctl("power", "off")

        await asyncio.sleep(1.0)

        _log.info("bluetoothctl power on")
        await _btctl("power", "on")

        # ── Step 4: wait for adapter to re-enumerate ─────────────────────────
        _log.info(f"Waiting {self._settle:.0f}s for adapter to stabilize ...")
        await asyncio.sleep(self._settle)

        logger.warning("=== Adapter restart complete — devices will reconnect ===")
