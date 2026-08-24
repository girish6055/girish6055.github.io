"""Event creation: cooldown/dedupe, snapshot capture, clip trigger, alert fan-out."""
import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import cv2
import numpy as np

from .alerts import AlertDispatcher
from .db import Database
from .paths import resolve
from .recorder import ClipRecorder, _safe

log = logging.getLogger(__name__)


class EventPublisher:
    def __init__(
        self,
        db: Database,
        dispatcher: AlertDispatcher,
        storage: Dict[str, Any],
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.db = db
        self.dispatcher = dispatcher
        self.storage = storage or {}
        self.snapshot_dir = resolve(self.storage.get("snapshots_dir", "snapshots"))
        self.snapshot_enabled = bool(self.storage.get("snapshot_on_event", True))
        self.on_event = on_event
        self._cooldowns: Dict[str, float] = {}
        self._lock = threading.Lock()
        self.recent: List[Dict[str, Any]] = []

    def in_cooldown(self, key: str, cooldown_seconds: float) -> bool:
        if cooldown_seconds <= 0:
            return False
        now = time.time()
        with self._lock:
            last = self._cooldowns.get(key, 0.0)
            return (now - last) < cooldown_seconds

    def _mark(self, key: str) -> None:
        with self._lock:
            self._cooldowns[key] = time.time()

    def emit(
        self,
        camera_id: str,
        camera_name: str,
        analytic: str,
        severity: str,
        title: str,
        message: str,
        zone: Optional[str] = None,
        track_ids: Optional[List[int]] = None,
        meta: Optional[Dict[str, Any]] = None,
        frame: Optional[np.ndarray] = None,
        dedupe_key: Optional[str] = None,
        cooldown_seconds: float = 0.0,
        recorder: Optional[ClipRecorder] = None,
    ) -> Optional[Dict[str, Any]]:
        key = f"{camera_id}:{analytic}:{dedupe_key or ''}"
        if self.in_cooldown(key, cooldown_seconds):
            return None
        self._mark(key)

        stamp = datetime.now()
        event: Dict[str, Any] = {
            "ts": stamp.isoformat(timespec="seconds"),
            "camera_id": camera_id,
            "camera_name": camera_name,
            "analytic": analytic,
            "severity": severity,
            "title": title,
            "message": message,
            "zone": zone,
            "track_ids": track_ids or [],
            "meta": meta or {},
            "snapshot": None,
            "clip": None,
        }

        if frame is not None and self.snapshot_enabled:
            event["snapshot"] = self._save_snapshot(frame, camera_id, analytic, stamp)
        if recorder is not None:
            event["clip"] = recorder.trigger(analytic)

        event["id"] = self.db.insert_event(event)
        self.dispatcher.send(event)
        with self._lock:
            self.recent.insert(0, event)
            del self.recent[200:]
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:  # noqa: BLE001
                log.exception("on_event callback failed")
        return event

    def _save_snapshot(self, frame: np.ndarray, camera_id: str, analytic: str, stamp: datetime) -> Optional[str]:
        try:
            day_dir = self.snapshot_dir / stamp.strftime("%Y-%m-%d")
            day_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{camera_id}_{_safe(analytic)}_{stamp.strftime('%H%M%S_%f')[:-3]}.jpg"
            path = day_dir / filename
            if not cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85]):
                log.error("Failed writing snapshot %s", path)
                return None
            return f"{stamp.strftime('%Y-%m-%d')}/{filename}"
        except Exception:  # noqa: BLE001
            log.exception("Snapshot capture failed for %s/%s", camera_id, analytic)
            return None
