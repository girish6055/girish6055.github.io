"""6. Machine idle timer (motion-based, with optional PLC/OPC override)."""
from typing import Optional

import cv2
import numpy as np

from ..config import AnalyticConfig, CameraConfig
from ..geometry import polygon_bounds
from .base import FrameContext, Services, WindowedAnalytic
from .draw import draw_polygon


class MachineIdleAnalytic(WindowedAnalytic):
    """Idle = no pixel motion inside the machine zone for ``idle_seconds``.

    Vision-based idle detection is a proxy for machine state. Where a PLC/OPC-UA
    tag is available, feed it through ``ctx.extra['machine_state']`` (see
    docs/INTEGRATION.md) and it overrides the motion estimate.
    """

    type_name = "machine_idle"
    title = "Machine idle timer"
    wanted_labels = ("person",)
    windows_key = "shift_windows"
    fire_inside_windows = True

    def __init__(self, camera: CameraConfig, config: AnalyticConfig, services: Services) -> None:
        super().__init__(camera, config, services)
        self.machine_name = str(config.get("machine_name", self.zone_name or camera.id))
        self.idle_seconds = float(config.get("idle_seconds", 300))
        self.motion_threshold = float(config.get("motion_threshold", 3.0))
        self.require_operator_absent = bool(config.get("require_operator_absent", False))
        self._previous_roi: Optional[np.ndarray] = None
        self._idle_since: Optional[float] = None
        self.motion_score = 0.0
        self.idle_for = 0.0

    def process(self, ctx: FrameContext) -> None:
        if not self.window_active(ctx.now):
            self.status_text = "outside shift"
            self._idle_since = None
            self._previous_roi = None
            return

        roi = self._roi(ctx)
        if roi is None or roi.size == 0:
            self.status_text = "zone not visible"
            return

        gray = cv2.GaussianBlur(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        if self._previous_roi is None or self._previous_roi.shape != gray.shape:
            self._previous_roi = gray
            return
        self.motion_score = float(np.mean(cv2.absdiff(self._previous_roi, gray)))
        self._previous_roi = gray

        external_state = (ctx.extra.get("machine_state") or {}).get(self.machine_name)
        if external_state in ("running", "idle"):
            running = external_state == "running"
        else:
            running = self.motion_score >= self.motion_threshold

        if self.require_operator_absent and self.detections_in_zone(ctx, ["person"]):
            running = True  # an operator at the machine counts as attended

        if running:
            self._idle_since = None
            self.idle_for = 0.0
            self.status_text = f"running (motion {self.motion_score:.2f})"
            return

        if self._idle_since is None:
            self._idle_since = ctx.ts
        self.idle_for = ctx.ts - self._idle_since
        self.status_text = f"idle {self.idle_for / 60:.1f} min"
        if self.idle_for < self.idle_seconds:
            return

        self.emit(
            ctx,
            title=f"Machine idle: {self.machine_name}",
            message=(
                f"{self.machine_name} has shown no activity for {self.idle_for / 60:.1f} minutes "
                f"(motion score {self.motion_score:.2f}, threshold {self.motion_threshold:.2f})."
            ),
            meta={
                "machine": self.machine_name,
                "idle_minutes": round(self.idle_for / 60, 2),
                "motion_score": round(self.motion_score, 3),
                "source": external_state or "vision",
            },
            dedupe_key=self.machine_name,
        )

    def _roi(self, ctx: FrameContext) -> Optional[np.ndarray]:
        polygon = ctx.zone_polygon(self.zone_name)
        if polygon is None:
            return ctx.frame
        height, width = ctx.frame.shape[:2]
        x1, y1, x2, y2 = polygon_bounds(polygon)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(width, x2), min(height, y2)
        if x2 <= x1 or y2 <= y1:
            return None
        return ctx.frame[y1:y2, x1:x2]

    def overlay(self, frame: np.ndarray, ctx: FrameContext) -> None:
        idle = self._idle_since is not None
        draw_polygon(frame, ctx.zone_polygon(self.zone_name), (0, 165, 255) if idle else (0, 200, 0),
                     label=f"{self.machine_name}: {self.status_text}", alpha=0.10)

    def state(self):
        data = super().state()
        data.update({"machine": self.machine_name, "motion_score": round(self.motion_score, 3),
                     "idle_minutes": round(self.idle_for / 60, 2)})
        return data
