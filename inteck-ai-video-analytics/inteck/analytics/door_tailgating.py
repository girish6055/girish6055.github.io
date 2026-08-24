"""8. Door access by more than 2 people (tailgating on one badge swipe)."""
from collections import deque
from typing import Deque, Tuple

import numpy as np

from ..config import AnalyticConfig, CameraConfig
from .base import Analytic, FrameContext, LineCrossingCounter, Services
from .draw import draw_line


class DoorTailgatingAnalytic(Analytic):
    type_name = "door_tailgating"
    title = "Door access with more than 2 people"
    wanted_labels = ("person",)

    def __init__(self, camera: CameraConfig, config: AnalyticConfig, services: Services) -> None:
        super().__init__(camera, config, services)
        self.max_people = int(config.get("max_people", 2))
        self.window_seconds = float(config.get("window_seconds", 12))
        self.counter = LineCrossingCounter(config.get("in_direction", "down"))
        self._recent: Deque[Tuple[float, int]] = deque()  # (ts, track_id)

    def process(self, ctx: FrameContext) -> None:
        line = ctx.line_points(self.line_name)
        if line is None:
            self.status_text = "line not configured"
            return

        for detection, bucket in self.counter.update(ctx.persons, line):
            if bucket == "in":
                self._recent.append((ctx.ts, detection.track_id or -1))

        cutoff = ctx.ts - self.window_seconds
        while self._recent and self._recent[0][0] < cutoff:
            self._recent.popleft()

        entered = len({track_id for _, track_id in self._recent})
        self.status_text = f"{entered} entry(s) in last {self.window_seconds:.0f}s"
        if entered <= self.max_people:
            return

        track_ids = sorted({track_id for _, track_id in self._recent if track_id >= 0})
        self.emit(
            ctx,
            title=f"Door access by {entered} people",
            message=(
                f"{entered} people passed the door line within {self.window_seconds:.0f}s "
                f"(limit {self.max_people}) - possible tailgating."
            ),
            track_ids=track_ids,
            meta={"people": entered, "limit": self.max_people, "window_seconds": self.window_seconds},
            dedupe_key="tailgating",
        )
        self._recent.clear()  # start a fresh window after alerting

    def overlay(self, frame: np.ndarray, ctx: FrameContext) -> None:
        line = ctx.line_points(self.line_name)
        breach = len({t for _, t in self._recent}) > self.max_people
        draw_line(frame, line, (0, 0, 255) if breach else (255, 200, 0), label=f"DOOR {self.status_text}")

    def state(self):
        data = super().state()
        data.update({"entries_in_window": len({t for _, t in self._recent}), "limit": self.max_people})
        return data
