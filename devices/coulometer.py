from __future__ import annotations

from typing import Optional

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

    def parse_frame(self, frame: bytes) -> Optional[dict]:
        import time

        now = time.time()

        if len(frame) < 5 or frame[0] != 0xBB or frame[-1] != 0xEE:
            return None

        result = {}
        got_current_frame = False
        got_capacity_frame = False

        try:
            if 0xD8 in frame and (0xC1 in frame or 0xC0 in frame):
                d8_idx = frame.index(0xD8)
                dir_idx = frame.index(0xC1) if 0xC1 in frame else frame.index(0xC0)

                curr_val = parse_decimal_bytes(frame[1:dir_idx], decimals=2, min_digits=3)
                if curr_val is not None:
                    self.last_current = -curr_val if frame[dir_idx] == 0xC1 else curr_val
                    result["current_a"] = self.last_current
                    got_current_frame = True

                power = parse_decimal_bytes(frame[dir_idx + 1:d8_idx], decimals=2, min_digits=3)
                if power is not None:
                    if abs(self.last_current) > 0.01:
                        self.last_voltage = round(power / abs(self.last_current), 2)
                        result["voltage_v"] = self.last_voltage

                    result["power_w"] = round(self.last_voltage * self.last_current, 2)

            cap_tag = 0xD2 if 0xD2 in frame else (0xD4 if 0xD4 in frame else None)
            if cap_tag is not None:
                ah_val = parse_decimal_bytes(frame[1:frame.index(cap_tag)], decimals=3, min_digits=4)
                if ah_val is not None:
                    self.last_remaining_ah = ah_val
                    self.last_capacity_update_time = now
                    got_capacity_frame = True

                    result["remaining_ah"] = ah_val

                    if self.config.battery_capacity_ah:
                        soc = max(0.0, min(1.0, ah_val / self.config.battery_capacity_ah))
                        result["soc"] = soc

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
            self.log(f"parse_frame error: {e}")
            return None