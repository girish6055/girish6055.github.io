"""Analytic logic tests driven by synthetic detections (no camera or GPU needed).

Run with:  python -m unittest discover -s tests -v
"""
import unittest
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

from inteck.analytics import REGISTRY, build_analytics
from inteck.analytics.base import FrameContext, Services
from inteck.config import AnalyticConfig, CameraConfig
from inteck.detector import Detection

FRAME = np.zeros((720, 1280, 3), dtype=np.uint8)


class FakePublisher:
    """Stands in for EventPublisher; records emissions and honours cooldowns."""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []
        self._last: Dict[str, float] = {}
        self.clock = 0.0

    def emit(self, **kwargs) -> Optional[Dict[str, Any]]:
        key = f"{kwargs['camera_id']}:{kwargs['analytic']}:{kwargs.get('dedupe_key') or ''}"
        cooldown = float(kwargs.get("cooldown_seconds") or 0)
        if cooldown > 0 and (self.clock - self._last.get(key, -1e9)) < cooldown:
            return None
        self._last[key] = self.clock
        event = {k: v for k, v in kwargs.items() if k not in ("frame", "recorder")}
        event["ts"] = datetime.now().isoformat(timespec="seconds")
        event["id"] = len(self.events) + 1
        self.events.append(event)
        return event


class FakeCounterDB:
    def __init__(self) -> None:
        self.counters: Dict[tuple, int] = {}

    def set_counter(self, camera_id, analytic, name, day, value):
        self.counters[(camera_id, analytic, name, day)] = value


def make_camera(**kwargs) -> CameraConfig:
    return CameraConfig(
        id=kwargs.get("id", "cam1"),
        name=kwargs.get("name", "Camera 1"),
        source="test",
        zones=kwargs.get("zones", {}),
        lines=kwargs.get("lines", {}),
        analytics=[],
    )


def make_analytic(type_name: str, camera: CameraConfig, publisher, db=None, **options):
    cls = REGISTRY[type_name]
    config = AnalyticConfig(type=type_name, enabled=True, options=options)
    return cls(camera, config, Services(publisher=publisher, recorder=None, detector=None, db=db))


def person(track_id: int, x: float, y: float, size: float = 60.0, label: str = "person") -> Detection:
    return Detection(label=label, cls_id=0, conf=0.9,
                     bbox=(x - size / 2, y - size, x + size / 2, y), track_id=track_id)


def context(camera, detections, ts, now=None, extra=None) -> FrameContext:
    return FrameContext(camera=camera, frame=FRAME.copy(), ts=ts,
                        now=now or datetime(2026, 8, 24, 3, 0, 0), detections=detections,
                        scale=(1.0, 1.0), extra=extra or {})


def run_frames(analytic, camera, publisher, detections, start=0.0, steps=10, step=1.0, now=None, extra=None):
    for index in range(steps):
        ts = start + index * step
        publisher.clock = ts
        items = detections(index) if callable(detections) else detections
        analytic.process(context(camera, items, ts, now=now, extra=extra))


ZONE = {"z": [[100, 100], [1100, 100], [1100, 700], [100, 700]]}


class CanteenTimingTests(unittest.TestCase):
    def test_alerts_outside_permitted_window(self):
        camera, publisher = make_camera(zones=ZONE), FakePublisher()
        analytic = make_analytic(
            "canteen_timing", camera, publisher, zone="z", dwell_seconds=5, cooldown_seconds=120,
            permitted_windows=[{"days": [0, 1, 2, 3, 4, 5, 6], "start": "12:30", "end": "13:30"}],
        )
        run_frames(analytic, camera, publisher, [person(1, 600, 500)],
                   steps=10, now=datetime(2026, 8, 24, 22, 15))
        self.assertEqual(len(publisher.events), 1)
        self.assertIn("outside the permitted", publisher.events[0]["message"])

    def test_silent_during_permitted_window(self):
        camera, publisher = make_camera(zones=ZONE), FakePublisher()
        analytic = make_analytic(
            "canteen_timing", camera, publisher, zone="z", dwell_seconds=5,
            permitted_windows=[{"days": list(range(7)), "start": "12:30", "end": "13:30"}],
        )
        run_frames(analytic, camera, publisher, [person(1, 600, 500)],
                   steps=10, now=datetime(2026, 8, 24, 12, 45))
        self.assertEqual(publisher.events, [])

    def test_person_outside_zone_is_ignored(self):
        camera, publisher = make_camera(zones=ZONE), FakePublisher()
        analytic = make_analytic("canteen_timing", camera, publisher, zone="z", dwell_seconds=3,
                                 permitted_windows=[])
        run_frames(analytic, camera, publisher, [person(1, 50, 90)], steps=10)
        self.assertEqual(publisher.events, [])


class RestrictedAreaTests(unittest.TestCase):
    def test_alert_outside_authorized_hours(self):
        camera, publisher = make_camera(zones=ZONE), FakePublisher()
        analytic = make_analytic(
            "restricted_area", camera, publisher, zone="z", dwell_seconds=3, cooldown_seconds=60,
            authorized_windows=[{"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "18:00"}],
        )
        run_frames(analytic, camera, publisher, [person(7, 500, 400)],
                   steps=6, now=datetime(2026, 8, 24, 2, 0))
        self.assertEqual(len(publisher.events), 1)
        self.assertEqual(publisher.events[0]["track_ids"], [7])

    def test_no_alert_during_authorized_hours(self):
        camera, publisher = make_camera(zones=ZONE), FakePublisher()
        analytic = make_analytic(
            "restricted_area", camera, publisher, zone="z", dwell_seconds=3,
            authorized_windows=[{"days": list(range(7)), "start": "09:00", "end": "18:00"}],
        )
        run_frames(analytic, camera, publisher, [person(7, 500, 400)],
                   steps=6, now=datetime(2026, 8, 24, 10, 0))
        self.assertEqual(publisher.events, [])


class SecurityPostTests(unittest.TestCase):
    def test_absence_alert_after_threshold(self):
        camera, publisher = make_camera(zones=ZONE), FakePublisher()
        analytic = make_analytic("security_post", camera, publisher, zone="z",
                                 absence_seconds=30, cooldown_seconds=300, duty_windows=[])
        run_frames(analytic, camera, publisher, [], steps=40, step=1.0)
        self.assertEqual(len(publisher.events), 1)
        self.assertGreaterEqual(publisher.events[0]["meta"]["empty_seconds"], 30)

    def test_guard_present_clears_timer(self):
        camera, publisher = make_camera(zones=ZONE), FakePublisher()
        analytic = make_analytic("security_post", camera, publisher, zone="z", absence_seconds=30)
        run_frames(analytic, camera, publisher,
                   lambda i: [] if i % 10 else [person(1, 500, 500)], steps=60)
        self.assertEqual(publisher.events, [])


class CrowdGatheringTests(unittest.TestCase):
    def test_three_people_close_together(self):
        camera, publisher = make_camera(zones=ZONE), FakePublisher()
        analytic = make_analytic("crowd_gathering", camera, publisher, zone="z", max_people=2,
                                 cluster_radius_px=200, dwell_seconds=10, cooldown_seconds=180)
        crowd = [person(1, 600, 500), person(2, 650, 510), person(3, 700, 505)]
        run_frames(analytic, camera, publisher, crowd, steps=15)
        self.assertEqual(len(publisher.events), 1)
        self.assertEqual(publisher.events[0]["meta"]["group_size"], 3)

    def test_three_people_spread_apart(self):
        camera, publisher = make_camera(zones=ZONE), FakePublisher()
        analytic = make_analytic("crowd_gathering", camera, publisher, zone="z", max_people=2,
                                 cluster_radius_px=120, dwell_seconds=5)
        spread = [person(1, 200, 300), person(2, 600, 300), person(3, 1000, 300)]
        run_frames(analytic, camera, publisher, spread, steps=15)
        self.assertEqual(publisher.events, [])

    def test_two_people_never_alert(self):
        camera, publisher = make_camera(zones=ZONE), FakePublisher()
        analytic = make_analytic("crowd_gathering", camera, publisher, zone="z", max_people=2,
                                 cluster_radius_px=300, dwell_seconds=2)
        run_frames(analytic, camera, publisher, [person(1, 600, 500), person(2, 620, 505)], steps=15)
        self.assertEqual(publisher.events, [])


class MobilePhoneTests(unittest.TestCase):
    def test_phone_held_by_person(self):
        camera, publisher = make_camera(zones=ZONE), FakePublisher()
        analytic = make_analytic("mobile_phone", camera, publisher, zone="z", conf=0.4,
                                 dwell_seconds=3, cooldown_seconds=120)
        holder = person(4, 600, 520, size=80)
        phone = Detection(label="cell phone", cls_id=67, conf=0.72,
                          bbox=(590, 460, 615, 495), track_id=99)
        run_frames(analytic, camera, publisher, [holder, phone], steps=8)
        self.assertEqual(len(publisher.events), 1)
        self.assertEqual(publisher.events[0]["track_ids"], [4])

    def test_low_confidence_phone_ignored(self):
        camera, publisher = make_camera(zones=ZONE), FakePublisher()
        analytic = make_analytic("mobile_phone", camera, publisher, zone="z", conf=0.6, dwell_seconds=2)
        phone = Detection(label="cell phone", cls_id=67, conf=0.41, bbox=(590, 460, 615, 495), track_id=99)
        run_frames(analytic, camera, publisher, [person(4, 600, 520), phone], steps=8)
        self.assertEqual(publisher.events, [])


class MachineIdleTests(unittest.TestCase):
    zones = {"m": [[300, 200], [900, 200], [900, 600], [300, 600]]}

    def test_static_scene_raises_idle(self):
        camera, publisher = make_camera(zones=self.zones), FakePublisher()
        analytic = make_analytic("machine_idle", camera, publisher, zone="m", machine_name="CNC-01",
                                 idle_seconds=60, motion_threshold=3.0, cooldown_seconds=600,
                                 shift_windows=[])
        run_frames(analytic, camera, publisher, [], steps=80, step=1.0)
        self.assertEqual(len(publisher.events), 1)
        self.assertEqual(publisher.events[0]["meta"]["machine"], "CNC-01")

    def test_plc_running_state_overrides_vision(self):
        camera, publisher = make_camera(zones=self.zones), FakePublisher()
        analytic = make_analytic("machine_idle", camera, publisher, zone="m", machine_name="CNC-01",
                                 idle_seconds=30, shift_windows=[])
        run_frames(analytic, camera, publisher, [], steps=80,
                   extra={"machine_state": {"CNC-01": "running"}})
        self.assertEqual(publisher.events, [])

    def test_outside_shift_is_quiet(self):
        camera, publisher = make_camera(zones=self.zones), FakePublisher()
        analytic = make_analytic("machine_idle", camera, publisher, zone="m", idle_seconds=10,
                                 shift_windows=[{"days": list(range(7)), "start": "08:00", "end": "20:00"}])
        run_frames(analytic, camera, publisher, [], steps=60, now=datetime(2026, 8, 24, 23, 30))
        self.assertEqual(publisher.events, [])


class PPEViolationTests(unittest.TestCase):
    def test_stays_quiet_without_model(self):
        camera, publisher = make_camera(zones=ZONE), FakePublisher()
        analytic = make_analytic("ppe_violation", camera, publisher, zone="z",
                                 model="models/definitely_missing.pt", required_ppe=["helmet"])
        run_frames(analytic, camera, publisher, [person(1, 600, 500)], steps=10)
        self.assertEqual(publisher.events, [])
        self.assertEqual(analytic.status_text, "model missing")

    def test_missing_helmet_detected(self):
        camera, publisher = make_camera(zones=ZONE), FakePublisher()
        analytic = make_analytic("ppe_violation", camera, publisher, zone="z",
                                 required_ppe=["helmet", "vest"], dwell_seconds=3,
                                 cooldown_seconds=180, every_n_frames=1)
        analytic._model_ready = True
        worker = person(5, 600, 560, size=120)
        vest = Detection(label="vest", cls_id=1, conf=0.8, bbox=(560, 470, 640, 530))
        bare_head = Detection(label="head", cls_id=2, conf=0.8, bbox=(575, 440, 625, 470))

        class StubModel:
            def predict(self, frame, conf=None):
                return [vest, bare_head]

        analytic._detector = StubModel()
        run_frames(analytic, camera, publisher, [worker], steps=8)
        self.assertEqual(len(publisher.events), 1)
        self.assertEqual(publisher.events[0]["meta"]["missing"], ["helmet"])

    def test_fully_equipped_worker_passes(self):
        camera, publisher = make_camera(zones=ZONE), FakePublisher()
        analytic = make_analytic("ppe_violation", camera, publisher, zone="z",
                                 required_ppe=["helmet"], dwell_seconds=2, every_n_frames=1)
        analytic._model_ready = True
        worker = person(5, 600, 560, size=120)
        helmet = Detection(label="helmet", cls_id=0, conf=0.9, bbox=(575, 440, 625, 470))

        class StubModel:
            def predict(self, frame, conf=None):
                return [helmet]

        analytic._detector = StubModel()
        run_frames(analytic, camera, publisher, [worker], steps=8)
        self.assertEqual(publisher.events, [])


LINE = {"door": [[400, 500], [900, 500]]}


class DoorTailgatingTests(unittest.TestCase):
    def test_three_people_through_door(self):
        camera, publisher = make_camera(lines=LINE), FakePublisher()
        analytic = make_analytic("door_tailgating", camera, publisher, line="door",
                                 in_direction="down", max_people=2, window_seconds=12,
                                 cooldown_seconds=60)
        # Each person starts above the line and steps below it.
        def frames(index):
            people = []
            for track_id in (1, 2, 3):
                y = 400 if index < track_id else 620
                people.append(person(track_id, 600 + track_id * 20, y))
            return people

        run_frames(analytic, camera, publisher, frames, steps=6, step=1.0)
        self.assertEqual(len(publisher.events), 1)
        self.assertEqual(publisher.events[0]["meta"]["people"], 3)

    def test_two_people_allowed(self):
        camera, publisher = make_camera(lines=LINE), FakePublisher()
        analytic = make_analytic("door_tailgating", camera, publisher, line="door",
                                 in_direction="down", max_people=2, window_seconds=12)

        def frames(index):
            return [person(track_id, 600 + track_id * 20, 400 if index < track_id else 620)
                    for track_id in (1, 2)]

        run_frames(analytic, camera, publisher, frames, steps=6)
        self.assertEqual(publisher.events, [])

    def test_entries_outside_window_do_not_stack(self):
        camera, publisher = make_camera(lines=LINE), FakePublisher()
        analytic = make_analytic("door_tailgating", camera, publisher, line="door",
                                 in_direction="down", max_people=2, window_seconds=5)

        def frames(index):
            # one person crosses every 10s - never three within the 5s window
            track_id = index // 10 + 1
            phase = index % 10
            return [person(track_id, 600, 400 if phase < 5 else 620)]

        run_frames(analytic, camera, publisher, frames, steps=40)
        self.assertEqual(publisher.events, [])


class CountingTests(unittest.TestCase):
    lines = {"count": [[200, 500], [1100, 500]]}

    def test_people_in_and_out(self):
        camera, publisher, db = make_camera(lines=self.lines), FakePublisher(), FakeCounterDB()
        analytic = make_analytic("people_counting", camera, publisher, db=db, line="count",
                                 in_direction="down", summary_minutes=0)

        def frames(index):
            entering = person(1, 500, 400 if index < 2 else 620)
            leaving = person(2, 800, 620 if index < 4 else 400)
            return [entering, leaving]

        run_frames(analytic, camera, publisher, frames, steps=8)
        self.assertEqual(analytic.counter.counts, {"in": 1, "out": 1})
        self.assertEqual(analytic.state()["inside"], 0)

    def test_vehicle_counting_ignores_people(self):
        camera, publisher, db = make_camera(lines=self.lines), FakePublisher(), FakeCounterDB()
        analytic = make_analytic("vehicle_counting", camera, publisher, db=db, line="count",
                                 in_direction="down", summary_minutes=0,
                                 classes=["car", "truck", "bus", "motorcycle", "bicycle"])

        def frames(index):
            car = person(10, 500, 400 if index < 2 else 620, size=120, label="car")
            walker = person(11, 800, 400 if index < 2 else 620)
            return [car, walker]

        run_frames(analytic, camera, publisher, frames, steps=6)
        self.assertEqual(analytic.counter.counts, {"in": 1, "out": 0})
        self.assertEqual(analytic.per_class.get("car_in"), 1)

    def test_counters_persist_to_database(self):
        camera, publisher, db = make_camera(lines=self.lines), FakePublisher(), FakeCounterDB()
        analytic = make_analytic("people_counting", camera, publisher, db=db, line="count",
                                 in_direction="down", summary_minutes=0)
        run_frames(analytic, camera, publisher,
                   lambda i: [person(1, 500, 400 if i < 2 else 620)], steps=30, step=1.0)
        values = {key[2]: value for key, value in db.counters.items()}
        self.assertEqual(values.get("in"), 1)


class BuildAnalyticsTests(unittest.TestCase):
    def test_registry_covers_all_ten_analytics(self):
        self.assertEqual(len(REGISTRY), 10)

    def test_build_from_camera_config_skips_disabled(self):
        camera = make_camera(zones=ZONE, lines=LINE)
        camera.analytics = [
            AnalyticConfig(type="restricted_area", enabled=True, options={"zone": "z"}),
            AnalyticConfig(type="door_tailgating", enabled=False, options={"line": "door"}),
        ]
        built = build_analytics(camera, Services(publisher=FakePublisher()))
        self.assertEqual([a.type_name for a in built], ["restricted_area"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
