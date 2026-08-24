"""Flask dashboard: live MJPEG views, event table, counters, alerts API."""
import logging
import time
from datetime import datetime, timedelta
from typing import Any

import cv2
from flask import Flask, Response, abort, jsonify, render_template, request, send_from_directory

from ..engine import AnalyticsEngine
from ..paths import bundle_root, resolve

log = logging.getLogger(__name__)


def create_app(engine: AnalyticsEngine) -> Flask:
    root = bundle_root() / "inteck" / "web"
    app = Flask(
        __name__,
        template_folder=str(root / "templates"),
        static_folder=str(root / "static"),
    )
    dashboard_cfg = engine.config.dashboard
    stream_quality = int(dashboard_cfg.get("stream_quality", 70))
    stream_width = int(dashboard_cfg.get("stream_width", 960))
    snapshots_dir = resolve(engine.config.storage.get("snapshots_dir", "snapshots"))
    recordings_dir = resolve(engine.config.storage.get("recordings_dir", "recordings"))

    @app.route("/")
    def index():
        return render_template(
            "dashboard.html",
            site=engine.config.site.get("name", "INTECK"),
            cameras=[
                {"id": worker.camera.id, "name": worker.camera.name,
                 "analytics": [a.title for a in worker.analytics]}
                for worker in engine.workers.values()
            ],
        )

    @app.route("/api/status")
    def api_status():
        return jsonify(engine.status())

    @app.route("/api/events")
    def api_events():
        events = engine.db.list_events(
            limit=min(int(request.args.get("limit", 100)), 500),
            offset=int(request.args.get("offset", 0)),
            camera_id=request.args.get("camera") or None,
            analytic=request.args.get("analytic") or None,
            severity=request.args.get("severity") or None,
            since=request.args.get("since") or None,
        )
        return jsonify({"events": events})

    @app.route("/api/events/<int:event_id>/ack", methods=["POST"])
    def api_ack(event_id: int):
        engine.db.acknowledge(event_id)
        return jsonify({"ok": True, "id": event_id})

    @app.route("/api/summary")
    def api_summary():
        today = datetime.now().strftime("%Y-%m-%d")
        since_24h = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
        return jsonify({
            "severity_24h": engine.db.count_events(since=since_24h),
            "by_analytic_24h": engine.db.events_by_analytic(since=since_24h),
            "counters_today": engine.db.get_counters(day=today),
            "day": today,
        })

    @app.route("/api/config")
    def api_config():
        redacted = _redact(engine.config.raw)
        return jsonify(redacted)

    @app.route("/stream/<camera_id>")
    def stream(camera_id: str):
        worker = engine.workers.get(camera_id)
        if worker is None:
            abort(404)
        return Response(
            _mjpeg(worker, stream_quality, stream_width),
            mimetype="multipart/x-mixed-replace; boundary=frame",
        )

    @app.route("/snapshots/<path:filename>")
    def snapshot(filename: str):
        return send_from_directory(str(snapshots_dir), filename)

    @app.route("/recordings/<path:filename>")
    def recording(filename: str):
        return send_from_directory(str(recordings_dir), filename)

    @app.route("/healthz")
    def healthz():
        online = sum(1 for worker in engine.workers.values() if worker.stream.status == "online")
        return jsonify({"ok": True, "cameras_online": online, "cameras": len(engine.workers)})

    return app


def _mjpeg(worker, quality: int, width: int):
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
    while True:
        frame = worker.annotated_frame()
        if frame is None:  # camera still connecting - hold the stream open
            time.sleep(0.3)
            continue
        if width and frame.shape[1] > width:
            scale = width / float(frame.shape[1])
            frame = cv2.resize(frame, (width, int(frame.shape[0] * scale)))
        ok, buffer = cv2.imencode(".jpg", frame, encode_params)
        if not ok:
            time.sleep(0.1)
            continue
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
        time.sleep(0.12)


SECRET_KEYS = {"access_token", "password", "token", "api_key"}


def _redact(value: Any) -> Any:
    """Keeps RTSP credentials and API tokens out of the dashboard API."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key.lower() in SECRET_KEYS:
                result[key] = "***" if item else ""
            elif key == "source" and isinstance(item, str):
                result[key] = _redact_url(item)
            else:
                result[key] = _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _redact_url(url: str) -> str:
    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    _, host = rest.rsplit("@", 1)
    return f"{scheme}://***:***@{host}"
