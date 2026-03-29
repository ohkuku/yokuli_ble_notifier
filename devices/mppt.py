from __future__ import annotations

import asyncio
import time
from typing import List

from devices.base import BaseBleDevice


def modbus_crc(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def build_cmd(hex_body: str) -> bytes:
    body = bytes.fromhex(hex_body)
    return body + modbus_crc(body)


def u16(payload: bytes, reg_index: int) -> int:
    start = 3 + reg_index * 2
    return int.from_bytes(payload[start:start + 2], "big")


class MpptDevice(BaseBleDevice):
    def __init__(self, app_config, device_config):
        super().__init__(app_config, device_config)

        self.data_buffer = bytearray()
        self.last_poll_time = 0.0

        commands = self.config.commands or {}
        unlock_hex = commands.get("unlock")
        read_all_hex = commands.get("read_all")

        if not self.config.write_uuid:
            raise ValueError("MPPT device requires write_uuid in config")

        if not unlock_hex or not read_all_hex:
            raise ValueError("MPPT device requires commands.unlock and commands.read_all in config")

        self.cmd_unlock = build_cmd(unlock_hex)
        self.cmd_read_all = build_cmd(read_all_hex)

    def notification_handler(self, characteristic, data: bytearray) -> None:
        self.data_buffer.extend(data)

        # 解锁响应：一般是 7 字节 01 03 02 xx xx crc crc 这种短包
        if len(self.data_buffer) == 7 and self.data_buffer[0] == 0x01 and self.data_buffer[1] == 0x03:
            self.log("Unlock response received.")
            self.mark_data_received()
            self.data_buffer.clear()
            return

        # 完整 15 寄存器读取响应长度：35 bytes
        if len(self.data_buffer) >= 35:
            start = self.data_buffer.find(b"\x01\x03\x1e")
            if start != -1 and len(self.data_buffer[start:]) >= 35:
                payload = bytes(self.data_buffer[start:start + 35])

                if payload[-2:] == modbus_crc(payload[:-2]):
                    parsed = self.parse_payload(payload)
                    self.mark_data_received()
                    self.log(f"Parsed: {parsed}")
                    self._queue_signalk(self._to_signalk(parsed))

                self.data_buffer.clear()

    async def on_after_connect(self) -> None:
        if self.client is None:
            raise RuntimeError("Client not connected")

        # 给设备一点缓冲时间
        await asyncio.sleep(1.0)

        self.log("Sending unlock command ...")
        await self.client.write_gatt_char(
            self.config.write_uuid,
            self.cmd_unlock,
            response=True,
        )

        self.last_poll_time = 0.0

    async def on_tick(self) -> None:
        if self.client is None or not self.client.is_connected:
            return

        poll_interval = self.config.poll_interval_seconds or 5
        now = time.time()

        if now - self.last_poll_time >= poll_interval:
            self.log("Sending read_all command ...")
            await self.client.write_gatt_char(
                self.config.write_uuid,
                self.cmd_read_all,
                response=True,
            )
            self.last_poll_time = now

    def parse_payload(self, payload: bytes) -> dict:
        regs = [u16(payload, i) for i in range(15)]

        return {
            "soc": regs[0],
            "bat_v": regs[1] / 10.0,
            "bat_a": regs[2] / 100.0,
            "pv_v": regs[7] / 10.0,
            "pv_a": regs[8] / 100.0,
            "pv_w": regs[9],
            "temp_c": regs[13] / 100.0,
        }

    def _to_signalk(self, parsed: dict) -> List[dict]:
        """Convert MPPT parsed data to Signal K path/value pairs.

        Strategy A: coulometer is the authoritative source for battery voltage
        and state of charge, so those paths are intentionally omitted here.
        MPPT contributes charging current, temperature, and all solar paths.
        """
        values: List[dict] = []
        # if "bat_v" in parsed:
        #     values.append({
        #         "path": "electrical.batteries.house.voltage",
        #         "value": parsed["bat_v"],
        #     })
        # if "bat_a" in parsed:
        #     values.append({
        #         "path": "electrical.batteries.house.current",
        #         "value": parsed["bat_a"],
        #     })
        # if "soc" in parsed:
        #     # Device returns 0-100 integer; Signal K expects 0.0-1.0
        #     values.append({
        #         "path": "electrical.batteries.house.capacity.stateOfCharge",
        #         "value": parsed["soc"] / 100.0,
        #     })
        if "temp_c" in parsed:
            # Signal K expects temperature in Kelvin
            values.append({
                "path": "electrical.batteries.house.temperature",
                "value": round(parsed["temp_c"] + 273.15, 2),
            })
        if "pv_v" in parsed:
            values.append({
                "path": "electrical.solar.house.voltage",
                "value": parsed["pv_v"],
            })
        if "pv_a" in parsed:
            values.append({
                "path": "electrical.solar.house.current",
                "value": parsed["pv_a"],
            })
        if "pv_w" in parsed:
            values.append({
                "path": "electrical.solar.house.panelPower",
                "value": parsed["pv_w"],
            })
        return values