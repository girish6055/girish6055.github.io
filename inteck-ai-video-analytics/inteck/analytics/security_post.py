"""3. Security-post absence (guard missing from post)."""
import numpy as np

from ..config import AnalyticConfig, CameraConfig
from .base import FrameContext, Services, WindowedAnalytic
from .draw import draw_polygon


class SecurityPostAnalytic(WindowedAnalytic):
    type_name = "security_post"
    title = "Security-post absence"
    wanted_labels = ("person",)
    windows_key = "duty_windows"
    fire_inside_windows = True  # only monitored while the post should be manned

    def __init__(self, camera: CameraConfig, config: AnalyticConfig, services: Services) -> None:
        super().__init__(camera, config, services)
        self.absence_seconds = float(config.get("absence_seconds", 120))
        self.min_guards = int(config.get("min_guards", 1))
        self._empty_since: float = 0.0

    def process(self, ctx: FrameContext) -> None:
        if not self.window_active(ctx.now):
            self.status_text = "off duty"
            self._empty_since = 0.0
            return

        guards = self.detections_in_zone(ctx, ["person"])
        if len(guards) >= self.min_guards:
            self._empty_since = 0.0
            self.status_text = f"manned ({len(guards)})"
            return

        if self._empty_since == 0.0:
            self._empty_since = ctx.ts
        empty_for = ctx.ts - self._empty_since
        self.status_text = f"unmanned {empty_for:.0f}s"
        if empty_for < self.absence_seconds:
            return

        self.emit(
            ctx,
            title="Security post unmanned",
            message=(
                f"No guard detected at '{self.zone_name}' for {empty_for:.0f}s "
                f"(threshold {self.absence_seconds:.0f}s)."
            ),
            meta={"empty_seconds": round(empty_for, 1), "min_guards": self.min_guards},
            dedupe_key="absence",
        )

    def overlay(self, frame: np.ndarray, ctx: FrameContext) -> None:
        polygon = ctx.zone_polygon(self.zone_name)
        unmanned = self._empty_since > 0.0
        draw_polygon(frame, polygon, (0, 0, 255) if unmanned else (0, 200, 0),
                     label=f"POST {self.status_text.upper()}")
