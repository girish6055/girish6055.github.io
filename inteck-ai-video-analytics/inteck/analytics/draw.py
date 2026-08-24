"""Overlay drawing helpers shared by every analytic."""
from typing import Optional, Sequence, Tuple

import cv2
import numpy as np

FONT = cv2.FONT_HERSHEY_SIMPLEX


def draw_polygon(
    frame: np.ndarray,
    polygon: Optional[Sequence[Tuple[float, float]]],
    color: Tuple[int, int, int],
    label: str = "",
    alpha: float = 0.15,
) -> None:
    if not polygon or len(polygon) < 3:
        return
    points = np.array([[int(x), int(y)] for x, y in polygon], dtype=np.int32)
    if alpha > 0:
        overlay = frame.copy()
        cv2.fillPoly(overlay, [points], color)
        cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)
    cv2.polylines(frame, [points], isClosed=True, color=color, thickness=2)
    if label:
        x, y = points[0]
        draw_label(frame, label, (int(x), max(18, int(y) - 8)), color)


def draw_line(
    frame: np.ndarray,
    line: Optional[Sequence[Tuple[float, float]]],
    color: Tuple[int, int, int],
    label: str = "",
) -> None:
    if not line or len(line) < 2:
        return
    p1 = (int(line[0][0]), int(line[0][1]))
    p2 = (int(line[1][0]), int(line[1][1]))
    cv2.line(frame, p1, p2, color, 3)
    if label:
        draw_label(frame, label, (p1[0], max(18, p1[1] - 10)), color)


def draw_label(
    frame: np.ndarray,
    text: str,
    origin: Tuple[int, int],
    color: Tuple[int, int, int],
    scale: float = 0.5,
) -> None:
    (tw, th), baseline = cv2.getTextSize(text, FONT, scale, 1)
    x, y = origin
    cv2.rectangle(frame, (x, y - th - baseline - 2), (x + tw + 6, y + 2), color, -1)
    cv2.putText(frame, text, (x + 3, y - baseline + 1), FONT, scale, (255, 255, 255), 1, cv2.LINE_AA)


def draw_detection(frame: np.ndarray, detection, color: Tuple[int, int, int] = (0, 255, 0)) -> None:
    x1, y1, x2, y2 = (int(v) for v in detection.bbox)
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    tag = detection.label
    if detection.track_id is not None:
        tag = f"{tag}#{detection.track_id}"
    draw_label(frame, f"{tag} {detection.conf:.2f}", (x1, max(18, y1 - 4)), color, 0.45)
