from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from devices.base import BaseBleDevice

from config_loader import BluetoothConfig

_log = logging.getLogger("adapter")


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
       run_connected_loop immediately and the finally-block calls
       BleakClient.disconnect().  This is the cleanest way to flush Python-
       level BLE state before tearing down the adapter.
    2. Wait 2 s for in-progress disconnections to complete.
    3. bluetoothctl disconnect <MAC> for every known device as a belt-and-
       suspenders system-level cleanup.
    4. Wait 1 s for BlueZ to process the disconnects.
    5. Run adapter_restart_command (e.g. sudo systemctl restart bluetooth).
    6. Wait adapter_settle_seconds (default 5 s) for the adapter to
       re-enumerate and be ready to accept new connections.
    """

    def __init__(
        self,
        bt_config: BluetoothConfig,
        all_devices: "List[BaseBleDevice]",
        adapter_settle_seconds: float = 5.0,
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
        # Setting _disconnected_event causes run_connected_loop to raise
        # ConnectionError, which lands in the except block and then the
        # finally block where BleakClient.disconnect() is called.  This is
        # the most orderly way to flush Python-level BLE state.
        _log.info("Signalling all devices to disconnect ...")
        for device in self._all_devices:
            if not device._stop_event.is_set():
                device._disconnected_event.set()

        # Give the run loops time to call BleakClient.disconnect().
        await asyncio.sleep(2.0)

        # ── Step 2: system-level disconnect via bluetoothctl ─────────────────
        # Belt-and-suspenders: ensures BlueZ releases connection state even
        # if a BleakClient.disconnect() silently failed.
        for device in self._all_devices:
            mac = device.config.mac
            _log.info(f"bluetoothctl disconnect {mac}")
            try:
                proc = await asyncio.create_subprocess_exec(
                    "bluetoothctl", "disconnect", mac,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except Exception as exc:
                _log.warning(f"bluetoothctl disconnect {mac} failed: {exc}")

        await asyncio.sleep(1.0)

        # ── Step 3: restart the adapter service ──────────────────────────────
        cmd = self._config.adapter_restart_command
        _log.info(f"Running adapter restart: {cmd}")
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            if proc.returncode != 0:
                _log.warning(
                    f"Restart command exited {proc.returncode}: "
                    f"{stderr_bytes.decode(errors='replace').strip()}"
                )
            else:
                _log.info("Adapter restart command succeeded.")
        except asyncio.TimeoutError:
            _log.error("Adapter restart command timed out after 30 s")
        except Exception as exc:
            _log.error(f"Adapter restart command failed: {exc}")

        # ── Step 4: wait for adapter to re-enumerate ─────────────────────────
        _log.info(f"Waiting {self._settle:.0f}s for adapter to stabilize ...")
        await asyncio.sleep(self._settle)

        logger.warning("=== Adapter restart complete — devices will reconnect ===")
