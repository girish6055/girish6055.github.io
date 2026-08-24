"""1. Canteen entry outside permitted timings."""
import numpy as np

from ..config import AnalyticConfig, CameraConfig
from .base import DwellTracker, FrameContext, Services, WindowedAnalytic
from .draw import draw_polygon


class CanteenTimingAnalytic(WindowedAnalytic):
    type_name = "canteen_timing"
    title = "Canteen entry outside permitted timings"
    wanted_labels = ("person",)
    windows_key = "permitted_windows"
    fire_inside_windows = False  # fires when the time is NOT a permitted window

    def __init__(self, camera: CameraConfig, config: AnalyticConfig, services: Services) -> None:
        super().__init__(camera, config, services)
        self.dwell = DwellTracker(self.dwell_seconds or 5.0)

    def process(self, ctx: FrameContext) -> None:
        if not self.window_active(ctx.now):
            self.status_text = "permitted hours"
            self.dwell.reset()
            return

        inside = self.detections_in_zone(ctx, ["person"])
        keys = [d.track_id for d in inside if d.track_id is not None]
        breached = self.dwell.update(keys, ctx.ts)
        self.status_text = f"off-hours - {len(inside)} in canteen"
        if not breached:
            return

        track_ids = sorted(breached)
        self.emit(
            ctx,
            title="Canteen entry outside permitted timings",
            message=(
                f"{len(track_ids)} person(s) detected in '{self.zone_name}' at "
                f"{ctx.now.strftime('%H:%M:%S')}, outside the permitted canteen windows."
            ),
            track_ids=track_ids,
            meta={"people": len(track_ids), "time": ctx.now.strftime("%H:%M:%S")},
            dedupe_key="off_hours",
        )

    def overlay(self, frame: np.ndarray, ctx: FrameContext) -> None:
        polygon = ctx.zone_polygon(self.zone_name)
        active = self.window_active(ctx.now)
        draw_polygon(frame, polygon, (0, 165, 255) if active else (0, 200, 0),
                     label=f"CANTEEN {'OFF-HOURS' if active else 'OPEN'}")
