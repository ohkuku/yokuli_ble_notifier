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
    Coordinates Bluetooth adapter restarts across multiple BLE devices.

    When any device accumulates too many consecutive failures, it calls
    maybe_restart().  Only one restart runs at a time (asyncio.Lock) and
    a cooldown prevents restart storms.

    Restart sequence
    ----------------
    1. System-level disconnect for every known MAC via ``bluetoothctl``.
       This tells BlueZ to release its connection state before we pull the
       rug out from under it, which avoids the "Operation already in
       progress" ghost-connection problem on the next reconnect attempt.
    2. Run adapter_restart_command (e.g. ``sudo systemctl restart bluetooth``).
    3. Wait adapter_settle_seconds for the adapter to re-enumerate and be
       ready to accept new connections (default 5 s).
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
        Trigger an adapter restart if enabled and the cooldown has elapsed.

        Returns True when a restart was actually performed (the caller should
        reset its fail_count).  Returns False when the restart is disabled,
        the cooldown is still active, or another device just performed one.
        """
        if not self._config.enable_adapter_restart:
            return False

        now = time.time()
        remaining = self._config.restart_cooldown_seconds - (now - self._last_restart_time)
        if remaining > 0:
            requesting_device._logger.info(
                f"Adapter restart skipped — cooldown {remaining:.0f}s remaining"
            )
            return False

        async with self._lock:
            # Re-check inside the lock: another device may have just restarted.
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

    async def _do_restart(self, requesting_device: "BaseBleDevice") -> None:
        logger = requesting_device._logger
        logger.warning("=== Bluetooth adapter restart triggered ===")

        # ── Step 1: System-level disconnect for each known MAC ──────────────
        # This asks BlueZ to cleanly release the connection before the service
        # is restarted.  Failures are non-fatal; the service restart will force
        # disconnection anyway.
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

        # Small pause to let BlueZ process the disconnects before the service
        # is torn down.
        await asyncio.sleep(1.0)

        # ── Step 2: Run the adapter restart command ──────────────────────────
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

        # ── Step 3: Wait for adapter to re-enumerate ─────────────────────────
        _log.info(f"Waiting {self._settle:.0f}s for adapter to stabilize ...")
        await asyncio.sleep(self._settle)

        logger.warning("=== Adapter restart complete ===")
