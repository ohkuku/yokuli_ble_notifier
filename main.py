import asyncio

from config_loader import load_config
from devices.coulometer import CoulometerDevice
from devices.mppt import MpptDevice


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

        tasks.append(asyncio.create_task(device.run(), name=key))

    if not tasks:
        print("No enabled devices found.")
        return

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())