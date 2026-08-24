# Integration guide

## WhatsApp alerts

```jsonc
"whatsapp": {
  "enabled": true,
  "provider": "meta_cloud_api",
  "phone_number_id": "123456789012345",
  "access_token": "EAAG...",
  "recipients": ["919876543210"],
  "timeout_seconds": 8
}
```

Get `phone_number_id` and a permanent access token from the Meta WhatsApp
Business Platform (Business Manager → WhatsApp → API Setup). Recipients are
E.164 numbers without `+`.

**What Meta allows:** free-form text only reaches a number inside a 24-hour
window opened by that person messaging your business number. Outside it, only
an **approved message template** is delivered. For continuous plant alerting,
create a template (e.g. `inteck_alert` with `{{1}}` severity, `{{2}}` camera,
`{{3}}` detail) and swap the `"type": "text"` payload in
`inteck/alerts.py::_send_whatsapp` for the template form:

```python
payload = {
    "messaging_product": "whatsapp",
    "to": recipient,
    "type": "template",
    "template": {
        "name": cfg.get("template", "inteck_alert"),
        "language": {"code": "en"},
        "components": [{"type": "body", "parameters": [
            {"type": "text", "text": event["severity"]},
            {"type": "text", "text": event["camera_name"]},
            {"type": "text", "text": event["message"][:900]},
        ]}],
    },
}
```

Twilio, Gupshup and 360dialog work the same way — same JSON event, different
URL and auth header.

## Generic webhook

```jsonc
"webhook": { "enabled": true, "url": "http://10.0.0.50:9000/inteck", "timeout_seconds": 5 }
```

Each event is POSTed as JSON:

```json
{
  "id": 412, "ts": "2026-08-24T22:14:07", "camera_id": "cam_canteen",
  "camera_name": "Canteen Entrance", "analytic": "canteen_timing",
  "severity": "warning", "title": "Canteen entry outside permitted timings",
  "message": "2 person(s) detected in 'canteen_area' ...",
  "zone": "canteen_area", "track_ids": [4, 9],
  "snapshot": "2026-08-24/cam_canteen_canteen_timing_221407_882.jpg",
  "clip": "recordings/2026-08-24/cam_canteen_canteen_timing_221407_882.mp4",
  "meta": {"people": 2, "time": "22:14:07"}
}
```

Point it at a Teams/Slack incoming-webhook relay, an ERP endpoint, or a Node-RED
flow. Delivery runs on a worker thread; a slow endpoint never stalls detection.

## PLC / OPC-UA machine state

`machine_idle` uses pixel motion by default. When the real machine state is
available, it wins. Feed it into the worker's shared state — one small script,
started alongside the engine:

```python
# poll_plc.py - run inside the same process (see inteck/main.py) or import and
# call from a thread you start after engine.start()
import threading, time

def pump_machine_state(engine, read_plc):
    def loop():
        while True:
            states = read_plc()             # {"CNC-01": "running"|"idle"}
            for worker in engine.workers.values():
                worker.shared_state["machine_state"] = states
            time.sleep(2)
    threading.Thread(target=loop, daemon=True).start()
```

`read_plc` is yours: `opcua`/`asyncua` for OPC-UA, `pymodbus` for Modbus TCP,
`snap7` for Siemens S7. Any machine not in the dictionary falls back to the
vision estimate.

## PPE model

Stock YOLO11 detects people, not helmets. To enable `ppe_violation`:

1. Collect 500–2000 frames from **your** cameras, at the angles and lighting the
   analytic will run in. Public datasets alone generalise poorly to a new site.
2. Label helmet / vest (and `head` / `no-vest` negatives) in Roboflow, CVAT or
   Label Studio.
3. Train: `yolo detect train data=ppe.yaml model=yolo11s.pt imgsz=640 epochs=100`
4. Copy `runs/detect/train/weights/best.pt` to `models\ppe.pt`.
5. Set `required_ppe` and `conf` on the analytic, then restart.

Recognised labels: `helmet`/`hardhat`, `vest`/`safety-vest`, `mask`, `gloves`,
`goggles`, `boots`, and their negatives `no-helmet`/`head`, `no-vest`, `no-mask`,
`no-gloves`. A PPE box counts for a person when at least 35 % of it falls inside
that person's box. Extend the maps in `inteck/analytics/ppe_violation.py` if your
dataset uses other names.

## Employee identification

Not included. Adding face or badge recognition means storing biometric templates
of named employees, which carries consent, retention and (in India) DPDP Act
obligations. The clean integration point is the access-control system: correlate
`door_tailgating` events with badge swipes by timestamp, which identifies the
badge holder without the plant holding a face database. If face recognition is
still wanted, it belongs in a separate module with its own consent and retention
policy.

## Adding your own analytic

```python
# inteck/analytics/my_check.py
from .base import Analytic, DwellTracker, FrameContext

class MyCheckAnalytic(Analytic):
    type_name = "my_check"
    title = "My check"
    wanted_labels = ("person",)

    def process(self, ctx: FrameContext) -> None:
        people = self.detections_in_zone(ctx, ["person"])
        if len(people) > int(self.config.get("limit", 5)):
            self.emit(ctx, title="Too many people", message=f"{len(people)} in zone",
                      track_ids=[p.track_id for p in people if p.track_id], dedupe_key="limit")
```

Register it in `inteck/analytics/__init__.py::REGISTRY`, then add
`{"type": "my_check", "zone": "...", "limit": 5}` to a camera. `wanted_labels`
narrows the detector's class filter, so unused classes cost nothing.
