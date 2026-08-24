"""Configuration loading and validation."""
import copy
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Any, Dict, List, Optional

from .paths import resolve

log = logging.getLogger(__name__)

DEFAULTS: Dict[str, Any] = {
    "site": {"name": "INTECK Site", "timezone": "Asia/Kolkata"},
    "engine": {
        "model": "models/yolo11n.pt",
        "tracker": "bytetrack.yaml",
        "device": "cpu",
        "imgsz": 640,
        "conf": 0.35,
        "iou": 0.5,
        "target_fps": 8,
        "half": False,
    },
    "storage": {
        "database": "logs/events.db",
        "snapshots_dir": "snapshots",
        "recordings_dir": "recordings",
        "log_file": "logs/inteck.log",
        "retention_days": 30,
        "clips": {"enabled": True, "pre_seconds": 4, "post_seconds": 6, "fps": 8},
        "snapshot_on_event": True,
    },
    "dashboard": {
        "host": "127.0.0.1",
        "port": 8080,
        "open_browser": True,
        "stream_quality": 70,
        "stream_width": 960,
    },
    "alerts": {
        "console": True,
        "webhook": {"enabled": False, "url": "", "timeout_seconds": 5},
        "whatsapp": {"enabled": False, "recipients": []},
        "min_severity": "info",
    },
    "cameras": [],
}

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


class ConfigError(Exception):
    pass


@dataclass
class TimeWindow:
    days: List[int]
    start: dtime
    end: dtime

    @classmethod
    def parse(cls, raw: Dict[str, Any]) -> "TimeWindow":
        days = raw.get("days")
        if days is None:
            days = list(range(7))
        return cls(days=[int(d) for d in days], start=_parse_time(raw["start"]), end=_parse_time(raw["end"]))

    def contains(self, moment: datetime) -> bool:
        if moment.weekday() not in self.days:
            return False
        current = moment.time()
        if self.start <= self.end:
            return self.start <= current <= self.end
        # window wraps past midnight (e.g. night shift 22:00 -> 06:00)
        return current >= self.start or current <= self.end


def _parse_time(value: str) -> dtime:
    try:
        hour, minute = str(value).strip().split(":")[:2]
        return dtime(int(hour), int(minute))
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"Invalid time value {value!r}, expected HH:MM") from exc


def parse_windows(raw: Optional[List[Dict[str, Any]]]) -> List[TimeWindow]:
    return [TimeWindow.parse(item) for item in (raw or [])]


def in_any_window(windows: List[TimeWindow], moment: datetime) -> bool:
    return any(window.contains(moment) for window in windows)


@dataclass
class AnalyticConfig:
    type: str
    enabled: bool = True
    options: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)


@dataclass
class CameraConfig:
    id: str
    name: str
    source: str
    enabled: bool = True
    rotate: int = 0
    zones: Dict[str, List[List[float]]] = field(default_factory=dict)
    lines: Dict[str, List[List[float]]] = field(default_factory=dict)
    reference_size: Optional[List[int]] = None
    analytics: List[AnalyticConfig] = field(default_factory=list)
    reconnect_seconds: float = 5.0
    read_timeout_seconds: float = 20.0


@dataclass
class AppConfig:
    raw: Dict[str, Any]
    site: Dict[str, Any]
    engine: Dict[str, Any]
    storage: Dict[str, Any]
    dashboard: Dict[str, Any]
    alerts: Dict[str, Any]
    cameras: List[CameraConfig]
    path: str = ""


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str = "config/config.json") -> AppConfig:
    config_path = resolve(path)
    if not config_path.exists():
        raise ConfigError(
            f"Configuration file not found: {config_path}\n"
            "Copy config/config.example.json to config/config.json and set your RTSP URLs."
        )
    with config_path.open("r", encoding="utf-8") as handle:
        try:
            raw = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"config.json is not valid JSON: {exc}") from exc

    merged = _deep_merge(DEFAULTS, raw)
    cameras = [_parse_camera(item) for item in merged.get("cameras", [])]
    _validate_unique_ids(cameras)

    return AppConfig(
        raw=merged,
        site=merged["site"],
        engine=merged["engine"],
        storage=merged["storage"],
        dashboard=merged["dashboard"],
        alerts=merged["alerts"],
        cameras=cameras,
        path=str(config_path),
    )


def _parse_camera(item: Dict[str, Any]) -> CameraConfig:
    for required in ("id", "source"):
        if not item.get(required):
            raise ConfigError(f"Camera entry is missing required field {required!r}: {item}")

    analytics = []
    for entry in item.get("analytics", []):
        if "type" not in entry:
            raise ConfigError(f"Analytic entry on camera {item['id']} is missing 'type'")
        options = {k: v for k, v in entry.items() if k not in ("type", "enabled")}
        analytics.append(
            AnalyticConfig(type=entry["type"], enabled=bool(entry.get("enabled", True)), options=options)
        )

    zones = {name: [[float(p[0]), float(p[1])] for p in poly] for name, poly in (item.get("zones") or {}).items()}
    lines = {}
    for name, pts in (item.get("lines") or {}).items():
        if len(pts) != 2:
            raise ConfigError(f"Line {name!r} on camera {item['id']} must have exactly 2 points")
        lines[name] = [[float(pts[0][0]), float(pts[0][1])], [float(pts[1][0]), float(pts[1][1])]]

    return CameraConfig(
        id=str(item["id"]),
        name=str(item.get("name", item["id"])),
        source=str(item["source"]),
        enabled=bool(item.get("enabled", True)),
        rotate=int(item.get("rotate", 0)),
        zones=zones,
        lines=lines,
        reference_size=[int(v) for v in item["reference_size"]] if item.get("reference_size") else None,
        analytics=analytics,
        reconnect_seconds=float(item.get("reconnect_seconds", 5.0)),
        read_timeout_seconds=float(item.get("read_timeout_seconds", 20.0)),
    )


def _validate_unique_ids(cameras: List[CameraConfig]) -> None:
    seen = set()
    for camera in cameras:
        if camera.id in seen:
            raise ConfigError(f"Duplicate camera id: {camera.id}")
        seen.add(camera.id)
