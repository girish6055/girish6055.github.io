"""2. Unauthorized entry into a restricted area."""
import numpy as np

from ..config import AnalyticConfig, CameraConfig
from .base import DwellTracker, FrameContext, Services, WindowedAnalytic
from .draw import draw_polygon


class RestrictedAreaAnalytic(WindowedAnalytic):
    type_name = "restricted_area"
    title = "Unauthorized restricted-area entry"
    wanted_labels = ("person",)
    windows_key = "authorized_windows"
    fire_inside_windows = False  # entry during authorized hours is fine

    def __init__(self, camera: CameraConfig, config: AnalyticConfig, services: Services) -> None:
        super().__init__(camera, config, services)
        self.dwell = DwellTracker(self.dwell_seconds or 3.0)
        self.always_alert = bool(config.get("always_alert", False))

    def process(self, ctx: FrameContext) -> None:
        if not self.always_alert and not self.window_active(ctx.now):
            self.status_text = "authorized hours"
            self.dwell.reset()
            return

        inside = self.detections_in_zone(ctx, ["person"])
        keys = [d.track_id for d in inside if d.track_id is not None]
        breached = self.dwell.update(keys, ctx.ts)
        self.status_text = f"{len(inside)} in restricted zone" if inside else "clear"
        if not breached:
            return

        track_ids = sorted(breached)
        self.emit(
            ctx,
            title="Unauthorized restricted-area entry",
            message=(
                f"{len(track_ids)} person(s) inside restricted zone '{self.zone_name}' "
                f"for more than {self.dwell.dwell_seconds:.0f}s."
            ),
            track_ids=track_ids,
            meta={"people": len(track_ids)},
            dedupe_key="entry",
        )

    def overlay(self, frame: np.ndarray, ctx: FrameContext) -> None:
        polygon = ctx.zone_polygon(self.zone_name)
        armed = self.always_alert or self.window_active(ctx.now)
        draw_polygon(frame, polygon, (0, 0, 255) if armed else (0, 200, 0),
                     label=f"RESTRICTED {'ARMED' if armed else 'OPEN'}")
