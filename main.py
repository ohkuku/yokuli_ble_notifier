import asyncio

from config_loader import load_config
from devices.coulometer import CoulometerDevice
from devices.mppt import MpptDevice
from signalk_sender import SignalKTcpServer


async def main():
    config = load_config("config.yaml")

    tasks = []

    for key, device_cfg in config.devices.items():
        if not device_cfg.enabled:
            continue

        if key == "coulometer":
            device = CoulometerDevice(config, device_cfg)
        elif key == "mppt":
            device = MpptDevice(config, device_cfg)
        else:
            print(f"Unknown device key: {key}, skipping.")
            continue

        signalk = SignalKTcpServer(
            port=device_cfg.tcp_port,
            vessel_id=config.app.vessel_id,
            source_label=device_cfg.source_label,
        )
        await signalk.start()
        device.signalk = signalk

        tasks.append(asyncio.create_task(device.run(), name=key))

        # Stagger BLE connection attempts to avoid BlueZ "Operation already
        # in progress" errors when multiple devices connect simultaneously.
        if len(tasks) < sum(1 for d in config.devices.values() if d.enabled):
            await asyncio.sleep(5)

    if not tasks:
        print("No enabled devices found.")
        return

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
