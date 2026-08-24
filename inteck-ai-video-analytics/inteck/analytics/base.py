"""Shared analytic scaffolding: frame context, dwell timers, line crossings."""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np

from ..config import AnalyticConfig, CameraConfig, TimeWindow, in_any_window, parse_windows
from ..detector import Detection
from ..geometry import (
    crossing_direction,
    direction_label,
    point_in_polygon,
    scale_polygon,
)

log = logging.getLogger(__name__)


@dataclass
class Services:
    publisher: Any
    recorder: Any = None
    detector: Any = None
    db: Any = None


@dataclass
class FrameContext:
    camera: CameraConfig
    frame: np.ndarray
    ts: float
    now: datetime
    detections: List[Detection]
    scale: Tuple[float, float] = (1.0, 1.0)
    extra: Dict[str, Any] = field(default_factory=dict)

    def by_label(self, labels: Iterable[str]) -> List[Detection]:
        wanted = {str(label).lower() for label in labels}
        return [d for d in self.detections if d.label in wanted]

    @property
    def persons(self) -> List[Detection]:
        return self.by_label(["person"])

    def zone_polygon(self, name: Optional[str]) -> Optional[List[Tuple[float, float]]]:
        if not name:
            return None
        polygon = self.camera.zones.get(name)
        if not polygon:
            return None
        sx, sy = self.scale
        return scale_polygon([(p[0], p[1]) for p in polygon], sx, sy)

    def line_points(self, name: Optional[str]) -> Optional[List[Tuple[float, float]]]:
        if not name:
            return None
        line = self.camera.lines.get(name)
        if not line:
            return None
        sx, sy = self.scale
        return [(line[0][0] * sx, line[0][1] * sy), (line[1][0] * sx, line[1][1] * sy)]


class Analytic:
    """Base class. Subclasses implement ``process`` and optionally ``overlay``."""

    type_name = "base"
    title = "Analytic"
    #: labels this analytic needs from the primary model
    wanted_labels: Sequence[str] = ("person",)

    def __init__(self, camera: CameraConfig, config: AnalyticConfig, services: Services) -> None:
        self.camera = camera
        self.config = config
        self.services = services
        self.severity = str(config.get("severity", "warning"))
        self.cooldown_seconds = float(config.get("cooldown_seconds", 60))
        self.zone_name: Optional[str] = config.get("zone")
        self.line_name: Optional[str] = config.get("line")
        self.dwell_seconds = float(config.get("dwell_seconds", 0))
        self.last_event_ts: Optional[str] = None
        self.event_count = 0
        self.status_text = "idle"

    # -- helpers -------------------------------------------------------
    def emit(
        self,
        ctx: FrameContext,
        title: str,
        message: str,
        track_ids: Optional[List[int]] = None,
        meta: Optional[Dict[str, Any]] = None,
        dedupe_key: Optional[str] = None,
        frame: Optional[np.ndarray] = None,
        severity: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        event = self.services.publisher.emit(
            camera_id=self.camera.id,
            camera_name=self.camera.name,
            analytic=self.type_name,
            severity=severity or self.severity,
            title=title,
            message=message,
            zone=self.zone_name,
            track_ids=track_ids or [],
            meta=meta or {},
            frame=frame if frame is not None else ctx.frame,
            dedupe_key=dedupe_key,
            cooldown_seconds=self.cooldown_seconds,
            recorder=self.services.recorder,
        )
        if event:
            self.event_count += 1
            self.last_event_ts = event["ts"]
        return event

    def in_zone(self, detection: Detection, polygon) -> bool:
        """No zone configured means the whole frame is the zone."""
        if polygon is None:
            return True
        return point_in_polygon(detection.anchor, polygon)

    def detections_in_zone(self, ctx: FrameContext, labels: Sequence[str] = ("person",)) -> List[Detection]:
        polygon = ctx.zone_polygon(self.zone_name)
        return [d for d in ctx.by_label(labels) if self.in_zone(d, polygon)]

    # -- interface -----------------------------------------------------
    def process(self, ctx: FrameContext) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def overlay(self, frame: np.ndarray, ctx: FrameContext) -> None:
        return None

    def state(self) -> Dict[str, Any]:
        return {
            "type": self.type_name,
            "title": self.title,
            "zone": self.zone_name,
            "line": self.line_name,
            "severity": self.severity,
            "status": self.status_text,
            "events": self.event_count,
            "last_event": self.last_event_ts,
        }


class WindowedAnalytic(Analytic):
    """Analytic gated by configured time windows (shift/permitted/duty hours)."""

    windows_key = "windows"
    #: when True the analytic fires INSIDE the windows, otherwise OUTSIDE them
    fire_inside_windows = True

    def __init__(self, camera: CameraConfig, config: AnalyticConfig, services: Services) -> None:
        super().__init__(camera, config, services)
        self.windows: List[TimeWindow] = parse_windows(config.get(self.windows_key))

    def window_active(self, now: datetime) -> bool:
        if not self.windows:
            # No windows configured means "always on" for either polarity:
            # no permitted hours -> every entry is off-hours; no shift hours
            # -> the machine is watched around the clock.
            return True
        inside = in_any_window(self.windows, now)
        return inside if self.fire_inside_windows else not inside


class DwellTracker:
    """Tracks how long each key has continuously satisfied a condition."""

    def __init__(self, dwell_seconds: float, grace_seconds: float = 1.5) -> None:
        self.dwell_seconds = float(dwell_seconds)
        self.grace_seconds = float(grace_seconds)
        self._since: Dict[Any, float] = {}
        self._last_seen: Dict[Any, float] = {}

    def update(self, active_keys: Iterable[Any], ts: float) -> Set[Any]:
        """Feeds the currently-active keys; returns those past the dwell time."""
        active = set(active_keys)
        for key in active:
            self._since.setdefault(key, ts)
            self._last_seen[key] = ts
        for key in list(self._since):
            if key not in active and ts - self._last_seen.get(key, ts) > self.grace_seconds:
                self._since.pop(key, None)
                self._last_seen.pop(key, None)
        if self.dwell_seconds <= 0:
            return set(active)
        return {key for key in active if ts - self._since.get(key, ts) >= self.dwell_seconds}

    def elapsed(self, key: Any, ts: float) -> float:
        return ts - self._since.get(key, ts)

    def reset(self, key: Any = None) -> None:
        if key is None:
            self._since.clear()
            self._last_seen.clear()
        else:
            self._since.pop(key, None)
            self._last_seen.pop(key, None)


class LineCrossingCounter:
    """Counts tracked objects crossing a line, keyed by travel direction."""

    def __init__(self, in_direction: str = "down") -> None:
        self.in_direction = str(in_direction).lower()
        self._last_point: Dict[int, Tuple[float, float]] = {}
        self.counts: Dict[str, int] = {"in": 0, "out": 0}

    def update(self, detections: Sequence[Detection], line) -> List[Tuple[Detection, str]]:
        """Returns [(detection, 'in'|'out')] for objects that crossed this frame."""
        crossings: List[Tuple[Detection, str]] = []
        if line is None:
            return crossings
        seen: Set[int] = set()
        for detection in detections:
            if detection.track_id is None:
                continue
            seen.add(detection.track_id)
            current = detection.anchor
            previous = self._last_point.get(detection.track_id)
            self._last_point[detection.track_id] = current
            if previous is None:
                continue
            sign = crossing_direction(previous, current, line)
            if not sign:
                continue
            moved = direction_label(sign, line)
            bucket = "in" if moved == self.in_direction else "out"
            self.counts[bucket] += 1
            crossings.append((detection, bucket))
        for track_id in list(self._last_point):
            if track_id not in seen and len(self._last_point) > 256:
                self._last_point.pop(track_id, None)
        return crossings

    def reset(self) -> None:
        self.counts = {"in": 0, "out": 0}
        self._last_point.clear()
