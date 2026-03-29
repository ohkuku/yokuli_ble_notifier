import asyncio
import logging

from adapter_restart import AdapterRestartCoordinator
from config_loader import load_config
from devices.coulometer import CoulometerDevice
from devices.mppt import MpptDevice
from signalk_sender import SignalKTcpServer
from status_server import StatusServer


async def main():
    config = load_config("config.yaml")

    log_level = getattr(logging, config.app.log_level.upper(), logging.INFO)
    if getattr(config.app, "enable_debug_log", False):
        log_level = logging.DEBUG

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    ble_connect_lock = asyncio.Lock()
    tasks: list[asyncio.Task] = []
    signalk_servers: list[SignalKTcpServer] = []
    devices: list = []
    status_server: StatusServer | None = None
    coordinator: AdapterRestartCoordinator | None = None

    try:
        # ── Phase 1: build device objects and start Signal K TCP servers ────
        for key, device_cfg in config.devices.items():
            if not device_cfg.enabled:
                continue

            if key == "coulometer":
                device = CoulometerDevice(config, device_cfg)
            elif key == "mppt":
                device = MpptDevice(config, device_cfg)
            else:
                logging.warning(f"Unknown device key: {key}, skipping.")
                continue

            signalk = SignalKTcpServer(
                port=device_cfg.tcp_port,
                vessel_id=config.app.vessel_id,
                source_label=device_cfg.source_label,
            )
            await signalk.start()

            device.signalk = signalk
            device.ble_connect_lock = ble_connect_lock

            signalk_servers.append(signalk)
            devices.append(device)

        if not devices:
            logging.info("No enabled devices found.")
            return

        # ── Phase 2: wire up shared services before any task starts ─────────
        if config.bluetooth.enable_adapter_restart:
            coordinator = AdapterRestartCoordinator(config.bluetooth, devices)
            for device in devices:
                device.adapter_restart = coordinator
            logging.info(
                f"Adapter auto-restart enabled "
                f"(cooldown {config.bluetooth.restart_cooldown_seconds}s, "
                f"command: {config.bluetooth.adapter_restart_command!r})"
            )

        status_server = StatusServer(
            port=config.app.status_port,
            devices=devices,
            bt_config=config.bluetooth,
            coordinator=coordinator,
        )
        await status_server.start()

        # ── Phase 3: start BLE tasks ─────────────────────────────────────────
        for device in devices:
            tasks.append(asyncio.create_task(device.run(), name=device.config.key))

        await asyncio.gather(*tasks)

    finally:
        logging.info("Shutting down ...")

        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        for server in signalk_servers:
            try:
                await server.stop()
            except Exception as e:
                logging.warning(f"Failed to stop Signal K server: {e}")

        if status_server is not None:
            try:
                await status_server.stop()
            except Exception as e:
                logging.warning(f"Failed to stop status server: {e}")

        logging.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())