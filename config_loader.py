from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, List, Dict, Any
import yaml


@dataclass
class AppConfig:
    vessel_id: str
    log_level: str


@dataclass
class BluetoothConfig:
    restart_cooldown_seconds: int
    enable_adapter_restart: bool
    adapter_restart_command: str


@dataclass
class DeviceConfig:
    key: str
    enabled: bool
    name: str
    source_label: str
    mac: str
    tcp_port: int
    notify_uuids: List[str]
    write_uuid: Optional[str]
    watchdog_timeout_seconds: int
    reconnect_delay_seconds: int
    max_fail_before_restart: int
    battery_capacity_ah: Optional[float] = None
    poll_interval_seconds: Optional[int] = None
    commands: Optional[Dict[str, str]] = None


@dataclass
class Config:
    app: AppConfig
    bluetooth: BluetoothConfig
    devices: Dict[str, DeviceConfig]


def _require(data: Dict[str, Any], key: str) -> Any:
    if key not in data:
        raise ValueError(f"Missing required config field: {key}")
    return data[key]


def _ensure_dict(value: Any, field_name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Config field '{field_name}' must be a mapping/object")
    return value


def _ensure_list_of_str(value: Any, field_name: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Config field '{field_name}' must be a list of strings")
    return value


def _ensure_optional_dict(value: Any, field_name: str) -> Optional[Dict[str, str]]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"Config field '{field_name}' must be a mapping/object")
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError(f"Config field '{field_name}' must contain only string keys and string values")
    return value


def load_config(path: str = "config.yaml") -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    raw = _ensure_dict(raw, "root")

    app_raw = _ensure_dict(_require(raw, "app"), "app")
    bt_raw = _ensure_dict(_require(raw, "bluetooth"), "bluetooth")
    devices_raw = _ensure_dict(_require(raw, "devices"), "devices")

    app = AppConfig(
        vessel_id=str(_require(app_raw, "vessel_id")),
        log_level=str(_require(app_raw, "log_level")),
    )

    bluetooth = BluetoothConfig(
        restart_cooldown_seconds=int(_require(bt_raw, "restart_cooldown_seconds")),
        enable_adapter_restart=bool(_require(bt_raw, "enable_adapter_restart")),
        adapter_restart_command=str(_require(bt_raw, "adapter_restart_command")),
    )

    devices: Dict[str, DeviceConfig] = {}

    for key, value in devices_raw.items():
        device_raw = _ensure_dict(value, f"devices.{key}")

        name = str(_require(device_raw, "name"))

        device = DeviceConfig(
            key=str(key),
            enabled=bool(_require(device_raw, "enabled")),
            name=name,
            source_label=str(device_raw.get("source_label", name)),
            mac=str(_require(device_raw, "mac")),
            tcp_port=int(_require(device_raw, "tcp_port")),
            notify_uuids=_ensure_list_of_str(_require(device_raw, "notify_uuids"), f"devices.{key}.notify_uuids"),
            write_uuid=(
                None
                if device_raw.get("write_uuid") is None
                else str(device_raw.get("write_uuid"))
            ),
            watchdog_timeout_seconds=int(_require(device_raw, "watchdog_timeout_seconds")),
            reconnect_delay_seconds=int(_require(device_raw, "reconnect_delay_seconds")),
            max_fail_before_restart=int(_require(device_raw, "max_fail_before_restart")),
            battery_capacity_ah=(
                None
                if device_raw.get("battery_capacity_ah") is None
                else float(device_raw.get("battery_capacity_ah"))
            ),
            poll_interval_seconds=(
                None
                if device_raw.get("poll_interval_seconds") is None
                else int(device_raw.get("poll_interval_seconds"))
            ),
            commands=_ensure_optional_dict(device_raw.get("commands"), f"devices.{key}.commands"),
        )

        devices[device.key] = device

    return Config(
        app=app,
        bluetooth=bluetooth,
        devices=devices,
    )