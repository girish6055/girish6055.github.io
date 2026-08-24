"""Threaded RTSP/file/USB capture with automatic reconnect.

The reader always keeps only the newest decoded frame, so analytics never
process a backlog after a stall - on RTSP a backlog is what makes a live view
drift minutes behind reality.
"""
import logging
import threading
import time
from typing import Optional, Tuple

import cv2
import numpy as np

from .config import CameraConfig

log = logging.getLogger(__name__)

STATUS_CONNECTING = "connecting"
STATUS_ONLINE = "online"
STATUS_OFFLINE = "offline"
STATUS_STOPPED = "stopped"


class CameraStream:
    def __init__(self, config: CameraConfig) -> None:
        self.config = config
        self.status = STATUS_STOPPED
        self.last_error = ""
        self.frames_read = 0
        self.fps = 0.0
        self.width = 0
        self.height = 0

        self._frame: Optional[np.ndarray] = None
        self._frame_ts = 0.0
        self._frame_index = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._capture: Optional[cv2.VideoCapture] = None

    def start(self) -> "CameraStream":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self.status = STATUS_CONNECTING
        self._thread = threading.Thread(target=self._run, name=f"camera-{self.config.id}", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.status = STATUS_STOPPED

    def read(self) -> Tuple[Optional[np.ndarray], float, int]:
        """Returns (frame_copy, timestamp, frame_index); frame is None when offline."""
        with self._lock:
            if self._frame is None:
                return None, 0.0, 0
            return self._frame.copy(), self._frame_ts, self._frame_index

    def _open(self) -> Optional[cv2.VideoCapture]:
        source = self.config.source
        target = int(source) if str(source).isdigit() else source
        capture = cv2.VideoCapture(target, cv2.CAP_FFMPEG) if isinstance(target, str) else cv2.VideoCapture(target)
        try:
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except cv2.error:
            pass
        if not capture.isOpened():
            capture.release()
            return None
        return capture

    def _run(self) -> None:
        backoff = self.config.reconnect_seconds
        fps_window_start = time.time()
        fps_window_frames = 0

        while not self._stop.is_set():
            if self._capture is None:
                self.status = STATUS_CONNECTING
                self._capture = self._open()
                if self._capture is None:
                    self.status = STATUS_OFFLINE
                    self.last_error = "Unable to open stream"
                    log.warning("Camera %s: cannot open source, retrying in %.0fs", self.config.id, backoff)
                    self._stop.wait(backoff)
                    backoff = min(backoff * 1.5, 30.0)
                    continue
                backoff = self.config.reconnect_seconds
                log.info("Camera %s (%s): stream opened", self.config.id, self.config.name)

            ok, frame = self._capture.read()
            if not ok or frame is None:
                self.last_error = "Frame read failed"
                self.status = STATUS_OFFLINE
                log.warning("Camera %s: read failed, reconnecting", self.config.id)
                self._capture.release()
                self._capture = None
                self._stop.wait(self.config.reconnect_seconds)
                continue

            if self.config.rotate:
                frame = _rotate(frame, self.config.rotate)

            now = time.time()
            with self._lock:
                self._frame = frame
                self._frame_ts = now
                self._frame_index += 1
                self.height, self.width = frame.shape[:2]

            self.frames_read += 1
            self.status = STATUS_ONLINE
            self.last_error = ""

            fps_window_frames += 1
            if now - fps_window_start >= 2.0:
                self.fps = fps_window_frames / (now - fps_window_start)
                fps_window_start = now
                fps_window_frames = 0

        if self._capture is not None:
            self._capture.release()
            self._capture = None
        self.status = STATUS_STOPPED

    def info(self) -> dict:
        return {
            "id": self.config.id,
            "name": self.config.name,
            "status": self.status,
            "fps": round(self.fps, 1),
            "frames_read": self.frames_read,
            "resolution": f"{self.width}x{self.height}" if self.width else "-",
            "last_error": self.last_error,
            "analytics": [a.type for a in self.config.analytics if a.enabled],
        }


def _rotate(frame: np.ndarray, degrees: int) -> np.ndarray:
    degrees = degrees % 360
    if degrees == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if degrees == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if degrees == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame
