"""Analytics engine: one worker thread per camera, one shared YOLO model."""
import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

from .alerts import AlertDispatcher
from .analytics import Analytic, FrameContext, Services, build_analytics
from .analytics.draw import draw_detection, draw_label
from .camera import CameraStream
from .config import AppConfig, CameraConfig
from .db import Database
from .detector import Detector, ModelNotAvailable
from .events import EventPublisher
from .recorder import ClipRecorder

log = logging.getLogger(__name__)


class CameraWorker(threading.Thread):
    def __init__(
        self,
        camera: CameraConfig,
        detector: Detector,
        publisher: EventPublisher,
        db: Database,
        storage: Dict[str, Any],
        target_fps: float = 8.0,
    ) -> None:
        super().__init__(name=f"worker-{camera.id}", daemon=True)
        self.camera = camera
        self.detector = detector
        self.stream = CameraStream(camera)
        self.recorder = ClipRecorder(camera.id, storage)
        self.services = Services(publisher=publisher, recorder=self.recorder, detector=detector, db=db)
        self.analytics: List[Analytic] = build_analytics(camera, self.services)
        self.target_fps = max(1.0, float(target_fps))
        self.shared_state: Dict[str, Any] = {}

        self._stop = threading.Event()
        self._annotated: Optional[np.ndarray] = None
        self._annotated_lock = threading.Lock()
        self._last_frame_index = -1
        self.processed = 0
        self.last_error = ""
        self.inference_ms = 0.0

    @property
    def class_filter(self) -> Optional[List[int]]:
        labels = set()
        for analytic in self.analytics:
            labels.update(analytic.wanted_labels)
        if not labels:
            return None
        ids = self.detector.class_ids_for(labels)
        return ids or None

    def start(self) -> "CameraWorker":  # type: ignore[override]
        self.stream.start()
        super().start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self.stream.stop()
        self.recorder.flush_all()

    def annotated_frame(self) -> Optional[np.ndarray]:
        with self._annotated_lock:
            return None if self._annotated is None else self._annotated.copy()

    def run(self) -> None:
        interval = 1.0 / self.target_fps
        class_filter = None
        resolved_filter = False

        while not self._stop.is_set():
            cycle_start = time.time()
            frame, frame_ts, frame_index = self.stream.read()
            if frame is None or frame_index == self._last_frame_index:
                self._stop.wait(min(interval, 0.2))
                continue
            self._last_frame_index = frame_index

            if not resolved_filter:
                try:
                    class_filter = self.class_filter
                    resolved_filter = True
                except Exception:  # noqa: BLE001 - model may not be loaded yet
                    class_filter = None

            try:
                started = time.time()
                detections = self.detector.track(frame, self.camera.id, classes=class_filter)
                self.inference_ms = (time.time() - started) * 1000.0
            except ModelNotAvailable as exc:
                self.last_error = str(exc)
                log.error("Camera %s: %s", self.camera.id, exc)
                self._stop.wait(5)
                continue
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"inference error: {exc}"
                log.exception("Camera %s: inference failed", self.camera.id)
                self._stop.wait(1)
                continue

            ctx = FrameContext(
                camera=self.camera,
                frame=frame,
                ts=frame_ts or time.time(),
                now=datetime.now(),
                detections=detections,
                scale=self._scale(frame),
                extra=self.shared_state,
            )

            for analytic in self.analytics:
                try:
                    analytic.process(ctx)
                except Exception:  # noqa: BLE001 - one bad analytic must not stop the camera
                    log.exception("Camera %s: analytic %s failed", self.camera.id, analytic.type_name)

            annotated = self._annotate(ctx)
            with self._annotated_lock:
                self._annotated = annotated
            self.recorder.push(annotated, ctx.ts)

            self.processed += 1
            self.last_error = ""
            elapsed = time.time() - cycle_start
            if elapsed < interval:
                self._stop.wait(interval - elapsed)

    def _scale(self, frame: np.ndarray) -> tuple:
        reference = self.camera.reference_size
        if not reference:
            return (1.0, 1.0)
        height, width = frame.shape[:2]
        ref_w, ref_h = reference[0] or width, reference[1] or height
        return (width / float(ref_w), height / float(ref_h))

    def _annotate(self, ctx: FrameContext) -> np.ndarray:
        frame = ctx.frame.copy()
        for analytic in self.analytics:
            try:
                analytic.overlay(frame, ctx)
            except Exception:  # noqa: BLE001
                log.exception("Camera %s: overlay for %s failed", self.camera.id, analytic.type_name)
        for detection in ctx.detections:
            colour = (0, 255, 0) if detection.label == "person" else (255, 160, 0)
            draw_detection(frame, detection, colour)
        stamp = ctx.now.strftime("%Y-%m-%d %H:%M:%S")
        draw_label(frame, f"{self.camera.name}  {stamp}  {self.inference_ms:.0f}ms",
                   (12, frame.shape[0] - 12), (40, 40, 40), 0.5)
        return frame

    def state(self) -> Dict[str, Any]:
        info = self.stream.info()
        info.update({
            "processed": self.processed,
            "inference_ms": round(self.inference_ms, 1),
            "worker_error": self.last_error,
            "analytics_state": [analytic.state() for analytic in self.analytics],
        })
        return info


class AnalyticsEngine:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.db = Database(config.storage.get("database", "logs/events.db"))
        self.dispatcher = AlertDispatcher(config.alerts)
        self.publisher = EventPublisher(self.db, self.dispatcher, config.storage)
        engine_cfg = config.engine
        self.detector = Detector(
            model_path=engine_cfg.get("model", "models/yolo11n.pt"),
            tracker=engine_cfg.get("tracker", "bytetrack.yaml"),
            device=engine_cfg.get("device", "cpu"),
            imgsz=int(engine_cfg.get("imgsz", 640)),
            conf=float(engine_cfg.get("conf", 0.35)),
            iou=float(engine_cfg.get("iou", 0.5)),
            half=bool(engine_cfg.get("half", False)),
        )
        self.workers: Dict[str, CameraWorker] = {}
        self.started_at: Optional[datetime] = None

    def start(self) -> None:
        try:
            self.detector.load()
        except ModelNotAvailable as exc:
            log.error("%s", exc)
            raise

        purged = self.db.purge_older_than(int(self.config.storage.get("retention_days", 30)))
        if purged:
            log.info("Purged %d events older than the retention window", purged)

        for camera in self.config.cameras:
            if not camera.enabled:
                log.info("Camera %s is disabled in config", camera.id)
                continue
            worker = CameraWorker(
                camera,
                self.detector,
                self.publisher,
                self.db,
                self.config.storage,
                target_fps=float(self.config.engine.get("target_fps", 8)),
            )
            self.workers[camera.id] = worker
            worker.start()
            log.info("Started camera %s (%s) with %d analytic(s)",
                     camera.id, camera.name, len(worker.analytics))
        self.started_at = datetime.now()

    def stop(self) -> None:
        for worker in self.workers.values():
            worker.stop()
        self.dispatcher.stop()
        self.db.close()

    def status(self) -> Dict[str, Any]:
        return {
            "site": self.config.site.get("name", "INTECK"),
            "started_at": self.started_at.isoformat(timespec="seconds") if self.started_at else None,
            "uptime_seconds": int((datetime.now() - self.started_at).total_seconds()) if self.started_at else 0,
            "model": self.detector.model_path,
            "device": self.detector.device,
            "cameras": [worker.state() for worker in self.workers.values()],
        }
