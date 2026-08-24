#!/usr/bin/env python3
"""Interactive zone / line editor.

Grabs one frame from a camera (or an image file) and lets you click the zone
polygon or counting line, then writes the coordinates straight into
config/config.json.

    python scripts/zone_editor.py --camera cam_gate --zone security_post
    python scripts/zone_editor.py --camera cam_gate --line people_line
    python scripts/zone_editor.py --image snapshots/2026-08-24/cam_gate.jpg --camera cam_gate --zone floor
    python scripts/zone_editor.py --camera cam_gate --grab-only     # just save a still

Controls:  left click = add point · right click / u = undo · c = clear
           s = save to config.json · q / Esc = quit without saving
"""
import argparse
import json
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from inteck.config import load_config  # noqa: E402
from inteck.paths import resolve  # noqa: E402

points = []


def on_mouse(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:
        points.append((x, y))
    elif event == cv2.EVENT_RBUTTONDOWN and points:
        points.pop()


def grab_frame(source: str):
    target = int(source) if str(source).isdigit() else source
    capture = cv2.VideoCapture(target)
    if not capture.isOpened():
        print(f"Could not open source: {source}")
        return None
    frame = None
    for _ in range(10):  # let the RTSP stream settle
        ok, candidate = capture.read()
        if ok:
            frame = candidate
    capture.release()
    return frame


def render(frame, is_line: bool, title: str):
    canvas = frame.copy()
    for index, point in enumerate(points):
        cv2.circle(canvas, point, 5, (0, 165, 255), -1)
        cv2.putText(canvas, str(index + 1), (point[0] + 8, point[1] - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)
    if len(points) >= 2:
        if is_line:
            cv2.line(canvas, points[0], points[1], (255, 255, 0), 2)
        else:
            cv2.polylines(canvas, [_as_array(points)], isClosed=True, color=(0, 165, 255), thickness=2)
    cv2.putText(canvas, title, (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    cv2.putText(canvas, "L-click add | R-click undo | c clear | s save | q quit",
                (12, canvas.shape[0] - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return canvas


def _as_array(pts):
    import numpy as np

    return np.array(pts, dtype="int32").reshape((-1, 1, 2))


def save(config_path: Path, camera_id: str, key: str, is_line: bool) -> None:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    for camera in data.get("cameras", []):
        if camera.get("id") != camera_id:
            continue
        bucket = "lines" if is_line else "zones"
        camera.setdefault(bucket, {})[key] = [[int(x), int(y)] for x, y in points]
        config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"Saved {bucket}.{key} for camera {camera_id} -> {config_path}")
        return
    print(f"Camera id {camera_id!r} was not found in {config_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="INTECK zone / line editor")
    parser.add_argument("--config", default="config/config.json")
    parser.add_argument("--camera", required=True, help="camera id from config.json")
    parser.add_argument("--zone", help="zone name to create or replace")
    parser.add_argument("--line", help="line name to create or replace (exactly 2 points)")
    parser.add_argument("--image", help="use this still image instead of the live camera")
    parser.add_argument("--grab-only", action="store_true", help="save a still frame and exit")
    args = parser.parse_args()

    config = load_config(args.config)
    camera = next((c for c in config.cameras if c.id == args.camera), None)
    if camera is None:
        print(f"Camera {args.camera!r} not found. Known: {', '.join(c.id for c in config.cameras)}")
        return 1

    frame = cv2.imread(args.image) if args.image else grab_frame(camera.source)
    if frame is None:
        print("No frame captured.")
        return 1

    if args.grab_only:
        out = resolve(f"snapshots/{camera.id}_reference.jpg")
        out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out), frame)
        print(f"Saved reference frame to {out} ({frame.shape[1]}x{frame.shape[0]}).")
        print("Set this resolution as 'reference_size' in config.json for this camera.")
        return 0

    if not args.zone and not args.line:
        print("Pass --zone NAME or --line NAME (or --grab-only).")
        return 1

    is_line = bool(args.line)
    key = args.line or args.zone
    existing = (camera.lines if is_line else camera.zones).get(key)
    if existing:
        points.extend([(int(x), int(y)) for x, y in existing])

    height, width = frame.shape[:2]
    print(f"Frame size {width}x{height}. Zone coordinates are stored in this resolution;")
    print("keep 'reference_size' in config.json matching it.")

    window = f"INTECK zone editor - {camera.name} - {'line' if is_line else 'zone'} '{key}'"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window, on_mouse)

    while True:
        cv2.imshow(window, render(frame, is_line, f"{camera.name} | {'LINE' if is_line else 'ZONE'} {key}"))
        pressed = cv2.waitKey(30) & 0xFF
        if pressed in (ord("q"), 27):
            print("Quit without saving.")
            break
        if pressed == ord("c"):
            points.clear()
        elif pressed == ord("u") and points:
            points.pop()
        elif pressed == ord("s"):
            if is_line and len(points) != 2:
                print("A line needs exactly 2 points.")
                continue
            if not is_line and len(points) < 3:
                print("A zone needs at least 3 points.")
                continue
            save(resolve(args.config), camera.id, key, is_line)
            break

    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
