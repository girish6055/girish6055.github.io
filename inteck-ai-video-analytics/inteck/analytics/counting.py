"""9 + 10. People counting and vehicle counting (line crossing, in/out)."""
from datetime import datetime
from typing import Dict, List

import numpy as np

from ..config import AnalyticConfig, CameraConfig
from ..detector import VEHICLE_LABELS
from .base import Analytic, FrameContext, LineCrossingCounter, Services
from .draw import draw_label, draw_line

PERSIST_INTERVAL_SECONDS = 10.0


class _CountingAnalytic(Analytic):
    counted_labels: List[str] = ["person"]
    noun = "person"

    def __init__(self, camera: CameraConfig, config: AnalyticConfig, services: Services) -> None:
        super().__init__(camera, config, services)
        labels = config.get("classes")
        self.counted_labels = [str(item).lower() for item in labels] if labels else list(self.counted_labels)
        self.counter = LineCrossingCounter(config.get("in_direction", "down"))
        self.reset_mode = str(config.get("reset", "daily")).lower()
        self.summary_minutes = float(config.get("summary_minutes", 60))
        self.event_per_crossing = bool(config.get("event_per_crossing", False))
        self.per_class: Dict[str, int] = {}
        self._day = datetime.now().strftime("%Y-%m-%d")
        self._last_persist = 0.0
        self._last_summary = 0.0

    def process(self, ctx: FrameContext) -> None:
        line = ctx.line_points(self.line_name)
        if line is None:
            self.status_text = "line not configured"
            return

        self._maybe_reset(ctx)
        crossings = self.counter.update(ctx.by_label(self.counted_labels), line)
        for detection, bucket in crossings:
            key = f"{detection.label}_{bucket}"
            self.per_class[key] = self.per_class.get(key, 0) + 1
            if self.event_per_crossing:
                self.emit(
                    ctx,
                    title=f"{self.noun.title()} counted ({bucket})",
                    message=f"{detection.label} #{detection.track_id} crossed '{self.line_name}' ({bucket}).",
                    track_ids=[detection.track_id] if detection.track_id is not None else [],
                    meta={"direction": bucket, "label": detection.label, "counts": dict(self.counter.counts)},
                    dedupe_key=f"{detection.track_id}_{bucket}",
                    severity="info",
                )

        counts = self.counter.counts
        self.status_text = f"in {counts['in']} / out {counts['out']} / inside {counts['in'] - counts['out']}"
        self._persist(ctx)
        self._maybe_summarise(ctx)

    def _maybe_reset(self, ctx: FrameContext) -> None:
        today = ctx.now.strftime("%Y-%m-%d")
        if self.reset_mode == "daily" and today != self._day:
            self._persist(ctx, force=True)
            self.counter.reset()
            self.per_class = {}
            self._day = today

    def _persist(self, ctx: FrameContext, force: bool = False) -> None:
        if not self.services.db:
            return
        if not force and ctx.ts - self._last_persist < PERSIST_INTERVAL_SECONDS:
            return
        self._last_persist = ctx.ts
        for name, value in self.counter.counts.items():
            self.services.db.set_counter(self.camera.id, self.type_name, name, self._day, value)
        for name, value in self.per_class.items():
            self.services.db.set_counter(self.camera.id, self.type_name, name, self._day, value)

    def _maybe_summarise(self, ctx: FrameContext) -> None:
        if self.summary_minutes <= 0:
            return
        if self._last_summary == 0.0:
            self._last_summary = ctx.ts
            return
        if ctx.ts - self._last_summary < self.summary_minutes * 60:
            return
        self._last_summary = ctx.ts
        counts = self.counter.counts
        if counts["in"] == 0 and counts["out"] == 0:
            return
        self.emit(
            ctx,
            title=f"{self.title} summary",
            message=(
                f"Last {self.summary_minutes:.0f} min on '{self.line_name}': "
                f"in {counts['in']}, out {counts['out']}, net {counts['in'] - counts['out']}."
            ),
            meta={"counts": dict(counts), "per_class": dict(self.per_class), "day": self._day},
            dedupe_key="summary",
            severity="info",
        )

    def overlay(self, frame: np.ndarray, ctx: FrameContext) -> None:
        line = ctx.line_points(self.line_name)
        draw_line(frame, line, (255, 255, 0), label=f"{self.noun.upper()} LINE")
        counts = self.counter.counts
        # Drawn bottom-left, above the timestamp, so it never collides with the
        # zone labels other analytics write along the top edge.
        offset = 40 if self.type_name == "people_counting" else 64
        draw_label(
            frame,
            f"{self.noun.title()}s IN {counts['in']}  OUT {counts['out']}  INSIDE {counts['in'] - counts['out']}",
            (12, max(20, frame.shape[0] - offset)),
            (255, 255, 0),
            0.55,
        )

    def state(self):
        data = super().state()
        data.update({
            "counts": dict(self.counter.counts),
            "inside": self.counter.counts["in"] - self.counter.counts["out"],
            "per_class": dict(self.per_class),
            "day": self._day,
        })
        return data


class PeopleCountingAnalytic(_CountingAnalytic):
    type_name = "people_counting"
    title = "People counting"
    wanted_labels = ("person",)
    counted_labels = ["person"]
    noun = "person"


class VehicleCountingAnalytic(_CountingAnalytic):
    type_name = "vehicle_counting"
    title = "Vehicle counting"
    wanted_labels = tuple(sorted(VEHICLE_LABELS))
    counted_labels = sorted(VEHICLE_LABELS)
    noun = "vehicle"

    def __init__(self, camera: CameraConfig, config: AnalyticConfig, services: Services) -> None:
        super().__init__(camera, config, services)
        self.wanted_labels = tuple(self.counted_labels)
