import asyncio
import logging

from config_loader import load_config
from devices.coulometer import CoulometerDevice
from devices.mppt import MpptDevice
from signalk_sender import SignalKTcpServer


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

    try:
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
            tasks.append(asyncio.create_task(device.run(), name=key))

        if not tasks:
            logging.info("No enabled devices found.")
            return

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

        logging.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())