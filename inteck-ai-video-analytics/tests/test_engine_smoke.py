"""End-to-end smoke test: synthetic video -> engine -> database -> dashboard API.

The YOLO model is replaced by a stub so this runs anywhere, but every other
piece is the real one: capture thread, worker loop, analytics, SQLite store,
snapshot writer and Flask routes.
"""
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import cv2
import numpy as np

import inteck.paths as paths


class EngineSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp(prefix="inteck-smoke-"))
        for name in ("config", "logs", "snapshots", "recordings", "models"):
            (cls.home / name).mkdir(parents=True, exist_ok=True)
        cls.video = cls.home / "sample.mp4"
        _write_sample_video(cls.video)
        cls._old_home = paths.app_root()
        import os
        os.environ["INTECK_HOME"] = str(cls.home)

        config = {
            "site": {"name": "Smoke Test Plant"},
            "engine": {"model": "models/stub.pt", "target_fps": 30, "device": "cpu"},
            "storage": {
                "database": "logs/events.db",
                "snapshots_dir": "snapshots",
                "recordings_dir": "recordings",
                "log_file": "logs/inteck.log",
                "clips": {"enabled": False},
            },
            "dashboard": {"open_browser": False},
            "alerts": {"console": False},
            "cameras": [{
                "id": "cam_test",
                "name": "Test Camera",
                "source": str(cls.video),
                "reference_size": [640, 360],
                "zones": {"restricted": [[0, 0], [640, 0], [640, 360], [0, 360]]},
                "lines": {"count_line": [[0, 180], [640, 180]]},
                "analytics": [
                    {"type": "restricted_area", "zone": "restricted", "dwell_seconds": 0,
                     "cooldown_seconds": 5, "authorized_windows": []},
                    {"type": "people_counting", "line": "count_line", "in_direction": "down",
                     "summary_minutes": 0},
                ],
            }],
        }
        (cls.home / "config" / "config.json").write_text(json.dumps(config), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        import os
        os.environ.pop("INTECK_HOME", None)
        shutil.rmtree(cls.home, ignore_errors=True)

    def test_engine_produces_events_and_serves_dashboard(self):
        from inteck.config import load_config
        from inteck.detector import Detection, Detector
        from inteck.engine import AnalyticsEngine
        from inteck.web.app import create_app

        # Stub the model: one person walking top-to-bottom across the count line.
        state = {"frame": 0}

        def fake_load(self):
            self.names = {0: "person"}

        def fake_track(self, frame, camera_id, classes=None):
            state["frame"] += 1
            y = min(340, 40 + state["frame"] * 60)
            return [Detection(label="person", cls_id=0, conf=0.9,
                              bbox=(300.0, float(y - 60), 360.0, float(y)), track_id=1)]

        original_load, original_track = Detector.load, Detector.track
        Detector.load, Detector.track = fake_load, fake_track
        try:
            config = load_config("config/config.json")
            engine = AnalyticsEngine(config)
            engine.start()
            counter = engine.workers["cam_test"].analytics[1].counter
            deadline = time.time() + 30
            while time.time() < deadline:
                if engine.db.list_events(limit=5) and counter.counts["in"] >= 1:
                    break
                time.sleep(0.3)

            events = engine.db.list_events(limit=20)
            self.assertTrue(events, "engine produced no events from the synthetic video")
            self.assertTrue(any(e["analytic"] == "restricted_area" for e in events))

            snapshot = events[0]["snapshot"]
            self.assertTrue(snapshot, "event has no snapshot path")
            self.assertTrue((self.home / "snapshots" / snapshot).exists())

            worker = engine.workers["cam_test"]
            self.assertGreater(worker.processed, 0)
            self.assertIsNotNone(worker.annotated_frame())

            app = create_app(engine)
            client = app.test_client()
            self.assertEqual(client.get("/").status_code, 200)

            status = client.get("/api/status").get_json()
            self.assertEqual(len(status["cameras"]), 1)
            self.assertEqual(status["cameras"][0]["id"], "cam_test")

            api_events = client.get("/api/events").get_json()["events"]
            self.assertTrue(api_events)

            summary = client.get("/api/summary").get_json()
            self.assertIn("severity_24h", summary)

            health = client.get("/healthz").get_json()
            self.assertEqual(health["cameras"], 1)

            counts = worker.analytics[1].counter.counts
            self.assertGreaterEqual(counts["in"], 1, "people counting did not register the crossing")
        finally:
            Detector.load, Detector.track = original_load, original_track
            try:
                engine.stop()
            except Exception:
                pass


def _write_sample_video(path: Path, frames: int = 120, size=(640, 360)) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 15.0, size)
    for index in range(frames):
        frame = np.full((size[1], size[0], 3), 30, dtype=np.uint8)
        y = 40 + index * 2
        cv2.rectangle(frame, (300, max(0, y - 60)), (360, min(size[1], y)), (200, 200, 200), -1)
        writer.write(frame)
    writer.release()


if __name__ == "__main__":
    unittest.main(verbosity=2)
