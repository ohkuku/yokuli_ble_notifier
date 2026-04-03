from __future__ import annotations

import logging
import time
from typing import List, Optional

from devices.base import BaseBleDevice


def parse_decimal_bytes(raw: bytes, decimals: int, min_digits: int = 1) -> Optional[float]:
    if not raw:
        return None

    s = raw.hex().zfill(max(min_digits, decimals + 1))
    try:
        return float(s) / (10 ** decimals)
    except Exception:
        return None


class CoulometerDevice(BaseBleDevice):
    def __init__(self, app_config, device_config):
        super().__init__(app_config, device_config)

        self.buffer = bytearray()
        self.last_voltage = 13.3
        self.last_current = 0.0
        self.last_remaining_ah: Optional[float] = None
        self.last_capacity_update_time: Optional[float] = None
        # Separate charge/discharge caches for net-current computation
        self._last_charge_a: Optional[float] = None    # from C0 frames, positive
        self._last_discharge_a: Optional[float] = None  # from C1 frames, negative

    def notification_handler(self, characteristic, data: bytearray) -> None:
        self.buffer.extend(data)

        while b"\xbb" in self.buffer and b"\xee" in self.buffer:
            start = self.buffer.find(b"\xbb")
            end = self.buffer.find(b"\xee", start)

            if end == -1:
                break

            frame = bytes(self.buffer[start:end + 1])
            self.buffer = self.buffer[end + 1:]

            parsed = self.parse_frame(frame)
            if parsed is not None:
                self.mark_data_received()
                self.log(f"Parsed: {parsed}")
                self._queue_signalk(self._to_signalk(parsed))

    def _is_plausible_measurement(
            self,
            current_a: Optional[float],
            voltage_v: Optional[float],
            power_w: Optional[float],
            frame: bytes,
    ) -> bool:
        """
        针对 12V 电池系统做基础合理性过滤，避免偶发错帧把
        2.4A @ 13.28V 解析成 13.28A @ 2.4V 这种情况。
        """
        if voltage_v is not None:
            # 12V 系统合理范围，按你现在的使用场景先写保守一点
            if not (10.0 <= voltage_v <= 15.5):
                self.log(
                    f"Suspicious frame dropped (voltage out of range): {frame.hex()} "
                    f"-> current={current_a}, voltage={voltage_v}, power={power_w}",
                    logging.WARNING,
                )
                return False

        if current_a is not None:
            # 放电约几安培，充电最高可达 20A+（太阳能），保守上限 30A
            if abs(current_a) > 30.0:
                self.log(
                    f"Suspicious frame dropped (current out of range): {frame.hex()} "
                    f"-> current={current_a}, voltage={voltage_v}, power={power_w}",
                    logging.WARNING,
                )
                return False

        if (
                current_a is not None
                and voltage_v is not None
                and power_w is not None
        ):
            expected_power = voltage_v * current_a
            # 给一些浮动空间，避免误杀
            if abs(expected_power - power_w) > max(5.0, abs(power_w) * 0.25):
                self.log(
                    f"Suspicious frame dropped (power mismatch): {frame.hex()} "
                    f"-> current={current_a}, voltage={voltage_v}, power={power_w}, "
                    f"expected_power={round(expected_power, 2)}",
                    logging.WARNING,
                )
                return False

        return True

    def parse_frame(self, frame: bytes) -> Optional[dict]:
        now = time.time()

        if len(frame) < 5 or frame[0] != 0xBB or frame[-1] != 0xEE:
            return None

        result: dict = {}

        got_current_frame = False
        got_capacity_frame = False

        # 先用局部变量，校验通过后再写入 self.last_*
        current_a: Optional[float] = None
        voltage_v: Optional[float] = None
        power_w: Optional[float] = None
        remaining_ah: Optional[float] = None
        soc: Optional[float] = None

        try:
            # 电流/功率帧
            if 0xD8 in frame and (0xC1 in frame or 0xC0 in frame):
                d8_idx = frame.index(0xD8)
                dir_idx = frame.index(0xC1) if 0xC1 in frame else frame.index(0xC0)

                # 基础边界保护，避免错切导致奇怪切片
                if dir_idx <= 1 or d8_idx <= dir_idx + 1:
                    self.log(
                        f"Suspicious frame dropped (bad indexes): {frame.hex()} "
                        f"-> dir_idx={dir_idx}, d8_idx={d8_idx}",
                        logging.WARNING,
                    )
                    return None

                val1 = parse_decimal_bytes(frame[1:dir_idx], decimals=2, min_digits=3)
                val2 = parse_decimal_bytes(frame[dir_idx + 1:d8_idx], decimals=2, min_digits=3)

                if val1 is not None and val2 is not None and val1 > 0.01:
                    if frame[dir_idx] == 0xC0:
                        # 充电帧: val1 = 电压, val2 = 功率 (绝对值)
                        charge_a = round(val2 / val1, 2)    # 正值
                        self._last_charge_a = charge_a
                        voltage_v = round(val1, 2)
                        # 净电流 = 太阳能充入 + 负载放出（两者同时存在时）
                        current_a = round(charge_a + (self._last_discharge_a or 0.0), 2)
                        power_w = round(current_a * voltage_v, 2)
                        result["charge_a"] = charge_a
                    else:
                        # 放电帧 (0xC1): val1 = 电流 (绝对值), val2 = 功率 (绝对值)
                        discharge_a = -val1                 # 负值
                        self._last_discharge_a = discharge_a
                        voltage_v = round(val2 / val1, 2)
                        # 净电流 = 太阳能充入 + 负载放出
                        current_a = round((self._last_charge_a or 0.0) + discharge_a, 2)
                        power_w = round(current_a * voltage_v, 2)
                        result["discharge_a"] = round(val1, 2)  # 放出电流（正值）
                    got_current_frame = True

                    if not self._is_plausible_measurement(current_a, voltage_v, power_w, frame):
                        return None

            # 容量帧 — 只用 D2 标记；D4 在 D2 帧内部是结构字段，单独出现时是无关帧
            if 0xD2 in frame:
                cap_idx = frame.index(0xD2)
                if cap_idx <= 1:
                    self.log(
                        f"Suspicious capacity frame dropped (bad index): {frame.hex()} "
                        f"-> cap_idx={cap_idx}",
                        logging.WARNING,
                    )
                    return None

                ah_val = parse_decimal_bytes(frame[1:cap_idx], decimals=3, min_digits=4)
                if ah_val is not None:
                    if (
                        self.config.battery_capacity_ah
                        and ah_val > self.config.battery_capacity_ah * 1.1
                    ):
                        # Junctek reports >capacity when battery is full — clamp to full
                        self.log(
                            f"[DEBUG] Capacity frame clamped to full: {frame.hex()} "
                            f"-> ah_val={ah_val} clamped to {self.config.battery_capacity_ah}",
                            logging.DEBUG,
                        )
                        ah_val = self.config.battery_capacity_ah
                    remaining_ah = ah_val
                    got_capacity_frame = True

                    if self.config.battery_capacity_ah:
                        soc = max(0.0, min(1.0, ah_val / self.config.battery_capacity_ah))

            # ===== 通过校验后，才更新缓存状态 =====
            if current_a is not None:
                self.last_current = current_a
                result["current_a"] = current_a

            if voltage_v is not None:
                self.last_voltage = voltage_v
                result["voltage_v"] = voltage_v

            if power_w is not None:
                result["power_w"] = power_w

            if remaining_ah is not None:
                self.last_remaining_ah = remaining_ah
                self.last_capacity_update_time = now
                result["remaining_ah"] = remaining_ah

                if soc is not None:
                    result["soc"] = soc

            # 只有 current frame、没有 capacity frame 时，用 current 做 Ah 估算
            if got_current_frame and not got_capacity_frame and self.last_remaining_ah is not None:
                if self.last_capacity_update_time is not None and self.config.battery_capacity_ah:
                    dt_hours = (now - self.last_capacity_update_time) / 3600.0
                    est_ah = max(
                        0.0,
                        min(
                            self.config.battery_capacity_ah,
                            self.last_remaining_ah + (self.last_current * dt_hours),
                            ),
                    )
                    self.last_remaining_ah = est_ah
                    self.last_capacity_update_time = now

                    result["remaining_ah"] = est_ah
                    result["soc"] = max(
                        0.0,
                        min(1.0, est_ah / self.config.battery_capacity_ah),
                    )

            return result if result else None

        except Exception as e:
            self.log(f"parse_frame error: {e}; frame={frame.hex()}", logging.WARNING)
            return None

    def _to_signalk(self, parsed: dict) -> List[dict]:
        """Convert coulometer parsed data to Signal K path/value pairs."""
        values: List[dict] = []

        if "voltage_v" in parsed:
            values.append({
                "path": "electrical.batteries.house.voltage",
                "value": parsed["voltage_v"],
            })

        if "current_a" in parsed:
            values.append({
                "path": "electrical.batteries.house.current",
                "value": parsed["current_a"],
            })

        if "charge_a" in parsed:
            values.append({
                "path": "electrical.batteries.house.current.charge",
                "value": parsed["charge_a"],
            })

        if "discharge_a" in parsed:
            values.append({
                "path": "electrical.batteries.house.current.discharge",
                "value": -parsed["discharge_a"],  # Signal K: negative = discharge
            })

        if "power_w" in parsed:
            values.append({
                "path": "electrical.batteries.house.power",
                "value": parsed["power_w"],
            })

        if "remaining_ah" in parsed:
            # Signal K expects remaining capacity in Joules (J = Ah * V * 3600)
            joules = round(parsed["remaining_ah"] * self.last_voltage * 3600, 2)
            values.append({
                "path": "electrical.batteries.house.capacity.remaining",
                "value": joules,
            })

        if "soc" in parsed:
            values.append({
                "path": "electrical.batteries.house.capacity.stateOfCharge",
                "value": parsed["soc"],
            })

        return values