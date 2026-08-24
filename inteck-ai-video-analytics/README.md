# INTECK AI Video Analytics

Multi-camera CCTV analytics for Windows 10/11, built on YOLO11 detection and
ByteTrack tracking. It reads RTSP streams, runs ten analytics per site
configuration, stores every event in a local SQLite database with a snapshot
(and optional video clip), and serves a live dashboard on the machine itself.

Everything runs locally — no cloud service, no video leaves the plant.

## The ten analytics

| # | `type` in config.json | What it detects |
|---|----------------------|-----------------|
| 1 | `canteen_timing` | A person in the canteen zone outside the permitted meal windows |
| 2 | `restricted_area` | A person in a restricted zone outside authorized hours |
| 3 | `security_post` | No guard at the security post for longer than the allowed gap |
| 4 | `crowd_gathering` | More than N people (default 2) clustered together for a sustained period |
| 5 | `mobile_phone` | A mobile phone visible in a no-phone zone, attributed to the nearest person |
| 6 | `machine_idle` | No activity in a machine zone during shift hours (PLC state can override) |
| 7 | `ppe_violation` | Missing helmet / vest / mask / gloves, via a site-trained PPE model |
| 8 | `door_tailgating` | More than N people through a door line inside one access window |
| 9 | `people_counting` | People in / out / currently inside, across a counting line |
| 10 | `vehicle_counting` | Vehicles in / out by class (car, truck, bus, motorcycle, bicycle) |

Each analytic is independent: enable only the ones a given camera needs, and give
each one its own zone, schedule, thresholds, severity and alert cooldown.

## Building the Windows executable

On the Windows PC that will run the system:

1. Install **Python 3.11 or 3.12, 64-bit** from python.org — tick *Add python.exe to PATH*.
2. Copy this folder to the machine (or extract the ZIP).
3. Edit `config\config.json` and set your real RTSP URLs (see below).
4. Double-click **`build_windows.bat`**.

The script creates a virtual environment, installs the dependencies (PyTorch is
a ~2 GB download the first time), fetches the YOLO11 weights, runs the
self-tests and compiles:

```
dist\INTECK_AI_Analytics\INTECK_AI_Analytics.exe
```

The build is a one-folder bundle: ship the whole `dist\INTECK_AI_Analytics`
folder, not just the .exe. `config\`, `models\`, `logs\`, `snapshots\` and
`recordings\` sit next to the executable and are read from there at runtime, so
an operator can change cameras or zones without rebuilding.

To run from source instead of compiling: `install_requirements.bat`, then
`run_from_source.bat`.

> **Note on the executable:** the .exe is a Windows PE binary and can only be
> produced on Windows. It is compiled by `build_windows.bat` on your machine;
> this repository holds the source, the build script and the spec file.

## First run

Double-click the .exe (or run `run_from_source.bat`). It:

1. loads `config/config.json`,
2. opens every enabled camera and starts one worker thread per camera,
3. serves the dashboard at <http://127.0.0.1:8080/> and opens a browser.

Validate a configuration before starting the cameras:

```
check_config.bat            or:  python run.py --check-config
python run.py --list-analytics
python run.py --no-dashboard        # headless, alerts only
python run.py --port 9000 --host 0.0.0.0   # expose on the LAN
```

## Configuring cameras

```jsonc
{
  "id": "cam_gate",
  "name": "Main Gate",
  "source": "rtsp://user:password@192.168.1.103:554/Streaming/Channels/101",
  "enabled": true,
  "rotate": 0,
  "reference_size": [1280, 720],      // resolution the zone coordinates were drawn at
  "zones":  { "security_post": [[60,320],[420,320],[420,700],[60,700]] },
  "lines":  { "people_line":   [[500,620],[1220,620]] },
  "analytics": [ /* one entry per analytic, see below */ ]
}
```

- `source` also accepts a video file path or a webcam index (`"0"`) — useful for
  testing an analytic against recorded footage before pointing it at a camera.
- Zone coordinates are pixel coordinates in `reference_size`; they are scaled
  automatically if the live stream resolution differs.
- Common RTSP paths: Hikvision `/Streaming/Channels/101`, Dahua
  `/cam/realmonitor?channel=1&subtype=0`, CP Plus `/cam/realmonitor?channel=1&subtype=1`.
  Use the sub-stream (channel `...02`, subtype `1`) for lighter CPU load.

### Drawing zones and lines

```
python scripts\zone_editor.py --camera cam_gate --grab-only            # save a reference still
python scripts\zone_editor.py --camera cam_gate --zone security_post   # click the polygon
python scripts\zone_editor.py --camera cam_gate --line people_line     # click two points
```

Left-click adds a point, right-click undoes, `s` writes it back into
`config/config.json`.

## Analytic options

Every analytic accepts `enabled`, `severity` (`info` / `warning` / `critical`)
and `cooldown_seconds` (minimum gap between repeat alerts of the same kind).

| Analytic | Key options |
|---|---|
| `canteen_timing` | `zone`, `permitted_windows[]`, `dwell_seconds` |
| `restricted_area` | `zone`, `authorized_windows[]`, `always_alert`, `dwell_seconds` |
| `security_post` | `zone`, `duty_windows[]`, `absence_seconds`, `min_guards` |
| `crowd_gathering` | `zone`, `max_people`, `cluster_radius_px`, `dwell_seconds` |
| `mobile_phone` | `zone`, `conf`, `dwell_seconds` |
| `machine_idle` | `zone`, `machine_name`, `idle_seconds`, `motion_threshold`, `shift_windows[]`, `require_operator_absent` |
| `ppe_violation` | `zone`, `required_ppe[]`, `model`, `conf`, `every_n_frames` |
| `door_tailgating` | `line`, `in_direction`, `max_people`, `window_seconds` |
| `people_counting` | `line`, `in_direction`, `reset`, `summary_minutes`, `event_per_crossing` |
| `vehicle_counting` | `line`, `in_direction`, `classes[]`, `reset`, `summary_minutes` |

A time window is `{"days": [0,1,2,3,4], "start": "09:00", "end": "18:00"}` where
Monday is 0. Windows may wrap past midnight (`"22:00"` → `"06:00"`). An empty
window list means "always monitored".

`in_direction` is `up`, `down`, `left` or `right` — the travel direction that
counts as entering. Watch the live view: the counter shows `IN` / `OUT` /
`INSIDE`, so a reversed line is obvious immediately.

## Alerts

`config.json → alerts`:

- `console` — alerts to the log file and console (always useful, on by default).
- `webhook` — POSTs the event JSON to any URL (Teams/Slack relay, ERP, SCADA).
- `whatsapp` — WhatsApp Cloud API. Set `phone_number_id`, `access_token` and
  `recipients` (E.164, e.g. `"919876543210"`). Note that outside the 24-hour
  customer-service window Meta only delivers **approved template messages**; the
  plain-text sender here works for testing and inside that window. See
  `docs/INTEGRATION.md`.
- `min_severity` — drop anything below this level.

Every event also lands in `logs/events.db`, with a JPEG in `snapshots/` and,
when clips are enabled, an MP4 in `recordings/` covering the seconds before and
after the event.

## Dashboard

- **Live cameras** — MJPEG views with zones, lines, tracks and counters drawn in.
- **Events** — filterable table with snapshot/clip links and acknowledgement.
- **Counting** — today's people and vehicle totals per camera.
- **System health** — per-camera status, FPS, inference time and each analytic's
  live state.

APIs: `/api/status`, `/api/events`, `/api/summary`, `/api/config` (credentials
redacted), `/healthz`, `/stream/<camera_id>`.

## Performance

CPU inference with `yolo11n` at 640 px handles roughly 3–5 cameras at 5–8 FPS on
a modern i5/i7. Beyond that use an NVIDIA GPU: install the CUDA build of PyTorch
and set `engine.device` to `"cuda"` (or `"0"`). Other levers: `target_fps`,
`imgsz`, camera sub-streams, and `yolo11s`/`yolo11m` when accuracy matters more
than speed.

## Tests

```
python -m unittest discover -s tests -t . -v
```

26 analytic tests drive synthetic detections through every one of the ten
analytics (both the alerting and the non-alerting path), plus an end-to-end test
that runs the real engine over a generated video and checks the database,
snapshot writer and dashboard routes. No camera or GPU needed.

## Accuracy notes, stated plainly

- **PPE** is a framework, not a finished detector. Stock YOLO11 has no helmet or
  vest class. Put a site-trained model at `models/ppe.pt`; until then the
  analytic reports `model missing` and stays silent rather than guessing.
- **Machine idle** infers state from pixel motion, which is a proxy. For
  billing-grade OEE, feed the real PLC/OPC-UA state (see `docs/INTEGRATION.md`);
  it overrides the vision estimate.
- **Counting** accuracy depends on line placement and camera angle. A line
  across the middle of the frame, perpendicular to the flow, with people fully
  visible for a second on both sides, is what makes it reliable.
- Thresholds in the shipped config are starting points. Expect to tune
  `dwell_seconds`, `cluster_radius_px` and `motion_threshold` against a day of
  real footage.

## Layout

```
config/config.json          site, cameras, zones, analytics, alerts
inteck/                     application package
  camera.py                 threaded RTSP capture with reconnect
  detector.py               YOLO11 + ByteTrack wrapper
  engine.py                 per-camera worker threads
  events.py / db.py         event pipeline and SQLite store
  alerts.py                 console / webhook / WhatsApp fan-out
  recorder.py               pre/post-event clip writer
  analytics/                the ten analytics + shared scaffolding
  web/                      Flask dashboard
scripts/zone_editor.py      interactive zone and line drawing
tests/                      unit + end-to-end tests
build_windows.bat           one-click Windows build
INTECK_AI_Analytics.spec    PyInstaller bundle definition
docs/                       operations and integration guides
```
