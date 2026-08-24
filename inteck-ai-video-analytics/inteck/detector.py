"""YOLO11 detection + ByteTrack tracking wrapper.

One model instance is shared by every camera; each camera keeps its own
tracker state through ultralytics' ``persist=True`` per-source tracking, so
track ids never collide between cameras.
"""
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .geometry import bbox_anchor
from .paths import resolve

log = logging.getLogger(__name__)

# COCO class ids used by the stock YOLO11 weights.
PERSON = 0
BICYCLE = 1
CAR = 2
MOTORCYCLE = 3
BUS = 5
TRUCK = 7
CELL_PHONE = 67

VEHICLE_LABELS = {"bicycle", "car", "motorcycle", "bus", "truck"}


@dataclass
class Detection:
    label: str
    cls_id: int
    conf: float
    bbox: Tuple[float, float, float, float]
    track_id: Optional[int] = None
    source: str = "primary"
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def anchor(self) -> Tuple[float, float]:
        return bbox_anchor(self.bbox, "bottom_center")

    @property
    def center(self) -> Tuple[float, float]:
        return bbox_anchor(self.bbox, "center")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "conf": round(self.conf, 3),
            "bbox": [round(v, 1) for v in self.bbox],
            "track_id": self.track_id,
        }


class ModelNotAvailable(RuntimeError):
    pass


class Detector:
    """Thin wrapper so analytics never touch ultralytics directly."""

    def __init__(
        self,
        model_path: str,
        tracker: str = "bytetrack.yaml",
        device: str = "cpu",
        imgsz: int = 640,
        conf: float = 0.35,
        iou: float = 0.5,
        half: bool = False,
    ) -> None:
        self.model_path = str(resolve(model_path))
        self.tracker = tracker
        self.device = device
        self.imgsz = imgsz
        self.conf = conf
        self.iou = iou
        self.half = half
        self._model = None
        self._lock = threading.Lock()
        self.names: Dict[int, str] = {}

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            from ultralytics import YOLO  # noqa: PLC0415 - heavy import, deferred on purpose
        except ImportError as exc:  # pragma: no cover - depends on install
            raise ModelNotAvailable(
                "ultralytics is not installed. Run build_windows.bat or "
                "'pip install -r requirements.txt' first."
            ) from exc

        from pathlib import Path

        path = Path(self.model_path)
        if not path.exists():
            # Ultralytics resolves a bare name like 'yolo11n.pt' by downloading it.
            log.warning("Model file %s not found; falling back to '%s'", path, path.name)
            self._model = YOLO(path.name)
        else:
            self._model = YOLO(str(path))
        self.names = dict(getattr(self._model, "names", {}) or {})
        log.info("Loaded model %s on device '%s' (%d classes)", self.model_path, self.device, len(self.names))

    def label_for(self, cls_id: int) -> str:
        return self.names.get(cls_id, str(cls_id))

    def class_ids_for(self, labels: Sequence[str]) -> List[int]:
        wanted = {str(label).lower() for label in labels}
        return sorted(cls_id for cls_id, name in self.names.items() if str(name).lower() in wanted)

    def track(self, frame: np.ndarray, camera_id: str, classes: Optional[Sequence[int]] = None) -> List[Detection]:
        """Runs detection + ByteTrack for one frame of one camera."""
        if self._model is None:
            self.load()
        with self._lock:
            results = self._model.track(
                source=frame,
                persist=True,
                tracker=self.tracker,
                classes=list(classes) if classes else None,
                conf=self.conf,
                iou=self.iou,
                imgsz=self.imgsz,
                device=self.device,
                half=self.half,
                verbose=False,
            )
        return self._to_detections(results)

    def predict(self, frame: np.ndarray, conf: Optional[float] = None) -> List[Detection]:
        """Detection without tracking - used by the auxiliary PPE model."""
        if self._model is None:
            self.load()
        with self._lock:
            results = self._model.predict(
                source=frame,
                conf=conf if conf is not None else self.conf,
                iou=self.iou,
                imgsz=self.imgsz,
                device=self.device,
                half=self.half,
                verbose=False,
            )
        return self._to_detections(results)

    def _to_detections(self, results) -> List[Detection]:
        detections: List[Detection] = []
        if not results:
            return detections
        result = results[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None or boxes.xyxy is None or len(boxes) == 0:
            return detections

        names = dict(getattr(result, "names", {}) or self.names)
        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy() if boxes.conf is not None else np.ones(len(xyxy))
        clss = boxes.cls.cpu().numpy().astype(int) if boxes.cls is not None else np.zeros(len(xyxy), dtype=int)
        ids = boxes.id.cpu().numpy().astype(int) if getattr(boxes, "id", None) is not None else [None] * len(xyxy)

        for box, conf, cls_id, track_id in zip(xyxy, confs, clss, ids):
            detections.append(
                Detection(
                    label=str(names.get(int(cls_id), cls_id)).lower(),
                    cls_id=int(cls_id),
                    conf=float(conf),
                    bbox=(float(box[0]), float(box[1]), float(box[2]), float(box[3])),
                    track_id=int(track_id) if track_id is not None else None,
                )
            )
        return detections
