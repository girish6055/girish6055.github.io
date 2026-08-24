"""5. Mobile-phone usage detection."""
from typing import Optional

import numpy as np

from ..config import AnalyticConfig, CameraConfig
from ..detector import Detection
from .base import Analytic, DwellTracker, FrameContext, Services
from .draw import draw_detection, draw_polygon


class MobilePhoneAnalytic(Analytic):
    type_name = "mobile_phone"
    title = "Mobile-phone detection"
    wanted_labels = ("person", "cell phone")

    def __init__(self, camera: CameraConfig, config: AnalyticConfig, services: Services) -> None:
        super().__init__(camera, config, services)
        self.min_conf = float(config.get("conf", 0.4))
        self.dwell = DwellTracker(self.dwell_seconds or 3.0, grace_seconds=2.0)
        self._phones = []

    def process(self, ctx: FrameContext) -> None:
        polygon = ctx.zone_polygon(self.zone_name)
        phones = [
            d for d in ctx.by_label(["cell phone"])
            if d.conf >= self.min_conf and self.in_zone(d, polygon)
        ]
        self._phones = phones
        people = ctx.persons

        # Key on the carrying person where possible so a phone that changes
        # hands does not reset the timer for the whole scene.
        keys = []
        holders = {}
        for phone in phones:
            holder = _nearest_person(phone, people)
            key = f"p{holder.track_id}" if holder is not None and holder.track_id is not None else f"t{phone.track_id}"
            keys.append(key)
            holders[key] = (phone, holder)

        breached = self.dwell.update(keys, ctx.ts)
        self.status_text = f"{len(phones)} phone(s) visible" if phones else "clear"
        for key in breached:
            phone, holder = holders.get(key, (None, None))
            if phone is None:
                continue
            track_ids = [holder.track_id] if holder is not None and holder.track_id is not None else []
            self.emit(
                ctx,
                title="Mobile phone in use",
                message=(
                    f"Mobile phone detected (confidence {phone.conf:.2f})"
                    + (f" with person #{holder.track_id}" if holder is not None and holder.track_id else "")
                    + (f" in zone '{self.zone_name}'." if self.zone_name else ".")
                ),
                track_ids=track_ids,
                meta={"confidence": round(phone.conf, 3), "phones_visible": len(phones)},
                dedupe_key=key,
            )

    def overlay(self, frame: np.ndarray, ctx: FrameContext) -> None:
        draw_polygon(frame, ctx.zone_polygon(self.zone_name), (200, 120, 0), label="PHONE WATCH", alpha=0.0)
        for phone in self._phones:
            draw_detection(frame, phone, (0, 0, 255))


def _nearest_person(phone: Detection, people) -> Optional[Detection]:
    px, py = phone.center
    best, best_distance = None, float("inf")
    for person in people:
        x1, y1, x2, y2 = person.bbox
        # Prefer a person whose box actually contains the phone.
        contained = x1 <= px <= x2 and y1 <= py <= y2
        cx, cy = person.center
        distance = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5 - (10_000 if contained else 0)
        if distance < best_distance:
            best, best_distance = person, distance
    return best
