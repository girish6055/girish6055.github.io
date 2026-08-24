"""Rolling pre/post-event clip recorder (one per camera)."""
import logging
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .paths import resolve

log = logging.getLogger(__name__)


class ClipRecorder:
    """Keeps the last ``pre_seconds`` of frames and writes a clip on trigger."""

    def __init__(self, camera_id: str, storage: Dict[str, Any]) -> None:
        clips = (storage or {}).get("clips") or {}
        self.enabled = bool(clips.get("enabled", True))
        self.fps = float(clips.get("fps", 8)) or 8.0
        self.pre_seconds = float(clips.get("pre_seconds", 4))
        self.post_seconds = float(clips.get("post_seconds", 6))
        self.camera_id = camera_id
        self.output_dir = resolve(storage.get("recordings_dir", "recordings"))

        maxlen = max(1, int(self.pre_seconds * self.fps))
        self._buffer: Deque[Tuple[float, np.ndarray]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._pending: List[Dict[str, Any]] = []

    def push(self, frame: np.ndarray, ts: float) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._buffer.append((ts, frame.copy()))
            for job in self._pending:
                job["frames"].append(frame.copy())
        self._flush_finished()

    def trigger(self, tag: str) -> Optional[str]:
        """Starts a clip; returns the relative path it will be written to."""
        if not self.enabled:
            return None
        stamp = datetime.now()
        day_dir = self.output_dir / stamp.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{self.camera_id}_{_safe(tag)}_{stamp.strftime('%H%M%S_%f')[:-3]}.mp4"
        path = day_dir / filename
        with self._lock:
            frames = [frame for _, frame in self._buffer]
            self._pending.append({"path": path, "frames": frames, "deadline": time.time() + self.post_seconds})
        return str(path.relative_to(self.output_dir.parent)) if self.output_dir.parent in path.parents else str(path)

    def _flush_finished(self) -> None:
        now = time.time()
        ready = []
        with self._lock:
            for job in list(self._pending):
                if now >= job["deadline"]:
                    self._pending.remove(job)
                    ready.append(job)
        for job in ready:
            self._write(job["path"], job["frames"])

    def flush_all(self) -> None:
        with self._lock:
            jobs, self._pending = self._pending, []
        for job in jobs:
            self._write(job["path"], job["frames"])

    def _write(self, path: Path, frames: List[np.ndarray]) -> None:
        if not frames:
            return
        height, width = frames[0].shape[:2]
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (width, height))
        if not writer.isOpened():
            log.error("Could not open video writer for %s", path)
            return
        try:
            for frame in frames:
                if frame.shape[:2] != (height, width):
                    frame = cv2.resize(frame, (width, height))
                writer.write(frame)
        finally:
            writer.release()
        log.info("Saved clip %s (%d frames)", path, len(frames))


def _safe(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(value))[:48]
