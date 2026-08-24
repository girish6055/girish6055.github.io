"""7. PPE violation framework (helmet / vest / mask / gloves).

The stock COCO YOLO11 weights have no PPE classes, so this analytic runs a
second, site-trained model. Point ``model`` at that weights file; the analytic
reports its own status as ``model missing`` and stays quiet until one exists,
rather than inventing detections from the person model.
"""
import logging
from typing import Dict, List, Optional

import numpy as np

from ..config import AnalyticConfig, CameraConfig
from ..detector import Detection, Detector, ModelNotAvailable
from ..paths import resolve
from .base import Analytic, DwellTracker, FrameContext, Services
from .draw import draw_detection, draw_label, draw_polygon

log = logging.getLogger(__name__)

# label -> the PPE item it satisfies / violates, for common PPE datasets
POSITIVE_LABELS = {
    "helmet": "helmet", "hardhat": "helmet", "hard-hat": "helmet", "safety-helmet": "helmet",
    "vest": "vest", "safety-vest": "vest", "reflective-vest": "vest",
    "mask": "mask", "face-mask": "mask",
    "gloves": "gloves", "glove": "gloves",
    "goggles": "goggles", "safety-glasses": "goggles",
    "boots": "boots", "safety-boots": "boots",
}
NEGATIVE_LABELS = {
    "no-helmet": "helmet", "no_helmet": "helmet", "nohardhat": "helmet", "no-hardhat": "helmet",
    "head": "helmet",  # bare head in the Hardhat/Head/Person dataset
    "no-vest": "vest", "no_vest": "vest", "no-safety-vest": "vest",
    "no-mask": "mask", "no_mask": "mask",
    "no-gloves": "gloves", "no-goggles": "goggles", "no-boots": "boots",
}


class PPEViolationAnalytic(Analytic):
    type_name = "ppe_violation"
    title = "PPE violation"
    wanted_labels = ("person",)

    def __init__(self, camera: CameraConfig, config: AnalyticConfig, services: Services) -> None:
        super().__init__(camera, config, services)
        self.required: List[str] = [str(item).lower() for item in (config.get("required_ppe") or ["helmet"])]
        self.model_path = str(config.get("model", "models/ppe.pt"))
        self.min_conf = float(config.get("conf", 0.45))
        self.every_n_frames = int(config.get("every_n_frames", 3))
        self.dwell = DwellTracker(self.dwell_seconds or 4.0, grace_seconds=3.0)
        self._detector: Optional[Detector] = None
        self._model_ready = resolve(self.model_path).exists()
        self._frame_no = 0
        self._last_ppe: List[Detection] = []
        self._violations: Dict[int, List[str]] = {}
        if not self._model_ready:
            log.warning(
                "PPE analytic on camera %s is inactive: model file %s not found. "
                "Train or download a PPE model and set 'model' in config.json.",
                camera.id, resolve(self.model_path),
            )

    def process(self, ctx: FrameContext) -> None:
        if not self._model_ready:
            self.status_text = "model missing"
            return
        if self._detector is None:
            self._detector = Detector(
                self.model_path,
                device=self.services.detector.device if self.services.detector else "cpu",
                imgsz=self.services.detector.imgsz if self.services.detector else 640,
                conf=self.min_conf,
            )
            try:
                self._detector.load()
            except ModelNotAvailable as exc:
                log.error("PPE model could not be loaded: %s", exc)
                self._model_ready = False
                self.status_text = "model load failed"
                return

        self._frame_no += 1
        if self._frame_no % max(1, self.every_n_frames) != 0:
            return

        people = self.detections_in_zone(ctx, ["person"])
        if not people:
            self.status_text = "no people in zone"
            self._violations = {}
            return

        self._last_ppe = self._detector.predict(ctx.frame, conf=self.min_conf)
        self._violations = {}
        keys = []
        for person in people:
            if person.track_id is None:
                continue
            missing = self._missing_ppe(person)
            if missing:
                self._violations[person.track_id] = missing
                keys.append(person.track_id)

        breached = self.dwell.update(keys, ctx.ts)
        self.status_text = (
            f"{len(self._violations)} violation(s) of {len(people)} people"
            if self._violations else f"{len(people)} compliant"
        )
        for track_id in sorted(breached):
            missing = self._violations.get(track_id, [])
            if not missing:
                continue
            self.emit(
                ctx,
                title="PPE violation",
                message=(
                    f"Person #{track_id} is missing required PPE: {', '.join(missing)}"
                    + (f" in zone '{self.zone_name}'." if self.zone_name else ".")
                ),
                track_ids=[track_id],
                meta={"missing": missing, "required": self.required},
                dedupe_key=f"person_{track_id}",
            )

    def _missing_ppe(self, person: Detection) -> List[str]:
        worn, absent = set(), set()
        for item in self._last_ppe:
            label = item.label.lower().replace(" ", "-")
            if not _overlaps(item, person):
                continue
            if label in POSITIVE_LABELS:
                worn.add(POSITIVE_LABELS[label])
            elif label in NEGATIVE_LABELS:
                absent.add(NEGATIVE_LABELS[label])
        missing = []
        for required in self.required:
            if required in absent or required not in worn:
                missing.append(required)
        return missing

    def overlay(self, frame: np.ndarray, ctx: FrameContext) -> None:
        draw_polygon(frame, ctx.zone_polygon(self.zone_name), (255, 0, 180), label="PPE ZONE", alpha=0.05)
        if not self._model_ready:
            draw_label(frame, "PPE MODEL NOT INSTALLED", (12, 66), (128, 128, 128), 0.5)
            return
        for item in self._last_ppe:
            colour = (0, 0, 255) if item.label.lower().replace(" ", "-") in NEGATIVE_LABELS else (0, 200, 0)
            draw_detection(frame, item, colour)

    def state(self):
        data = super().state()
        data.update({"model": self.model_path, "model_ready": self._model_ready, "required_ppe": self.required})
        return data


def _overlaps(item: Detection, person: Detection, min_ratio: float = 0.35) -> bool:
    """True when most of the PPE box falls inside the person box."""
    ix1 = max(item.bbox[0], person.bbox[0])
    iy1 = max(item.bbox[1], person.bbox[1])
    ix2 = min(item.bbox[2], person.bbox[2])
    iy2 = min(item.bbox[3], person.bbox[3])
    if ix2 <= ix1 or iy2 <= iy1:
        return False
    intersection = (ix2 - ix1) * (iy2 - iy1)
    item_area = max(1.0, (item.bbox[2] - item.bbox[0]) * (item.bbox[3] - item.bbox[1]))
    return intersection / item_area >= min_ratio
