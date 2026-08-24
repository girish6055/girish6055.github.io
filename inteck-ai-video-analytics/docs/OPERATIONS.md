# Operations guide

Day-to-day running of INTECK AI Video Analytics on the plant PC.

## Starting and stopping

- **Start:** double-click `INTECK_AI_Analytics.exe` (or `run_from_source.bat`).
  A console window shows the log; closing it stops the system.
- **Stop:** press `Ctrl+C` in the console, or close the window.
- **Dashboard:** <http://127.0.0.1:8080/> — opens automatically unless
  `dashboard.open_browser` is `false`.

### Run it as a Windows service (auto-start at boot)

Using NSSM (https://nssm.cc):

```
nssm install INTECK "C:\INTECK\dist\INTECK_AI_Analytics\INTECK_AI_Analytics.exe" --no-browser
nssm set INTECK AppDirectory "C:\INTECK\dist\INTECK_AI_Analytics"
nssm set INTECK Start SERVICE_AUTO_START
nssm start INTECK
```

Or Task Scheduler: *Create Task → Run whether user is logged on or not →
Trigger: At startup → Action: start the .exe with argument `--no-browser`*.

## Daily checks

1. **System health** tab — every camera `online`, FPS above ~4, no red errors.
2. **Events** tab — acknowledge what has been actioned; the row dims once acked.
3. **Counting** tab — totals reset at midnight (`"reset": "daily"`).

## Where things are stored

| Path | Contents |
|---|---|
| `logs\inteck.log` | Rotating application log (8 MB × 5 files) |
| `logs\events.db` | SQLite event and counter database |
| `snapshots\YYYY-MM-DD\` | One annotated JPEG per event |
| `recordings\YYYY-MM-DD\` | Pre/post-event MP4 clips |

`storage.retention_days` (default 30) purges old rows at startup. Snapshots and
clips are **not** auto-deleted — schedule a cleanup task if disk is tight:

```
forfiles /p "C:\INTECK\dist\INTECK_AI_Analytics\snapshots" /s /m *.jpg /d -30 /c "cmd /c del @path"
```

Query the database directly for reports:

```sql
SELECT date(ts) AS day, analytic, COUNT(*) FROM events GROUP BY day, analytic ORDER BY day DESC;
SELECT * FROM counters WHERE day = date('now');
```

## Tuning

| Symptom | Fix |
|---|---|
| Too many alerts for one incident | Raise `cooldown_seconds`, raise `dwell_seconds` |
| Alerts missed for brief events | Lower `dwell_seconds`, raise `target_fps` |
| Crowd alerts on people just walking past | Raise `dwell_seconds` (20–30 s), reduce `cluster_radius_px` |
| Idle machine reported while running | Lower `motion_threshold`, or tighten the zone to the moving part |
| Counting double-counts | Move the line away from the frame edge, prefer a straight-on view |
| High CPU | Use the camera sub-stream, lower `target_fps` / `imgsz`, disable unused analytics |

Changes to `config.json` take effect on restart.

## Troubleshooting

**Camera stays `connecting` / `offline`**
Test the URL in VLC (*Media → Open Network Stream*). Check credentials, that
the NVR allows extra RTSP sessions, and that Windows Firewall allows the app.
The system reconnects automatically with backoff; `logs\inteck.log` records
each attempt.

**`ultralytics is not installed`**
Run `install_requirements.bat`, or rebuild with `build_windows.bat`.

**Model file not found**
`python setup_models.py` downloads `models\yolo11n.pt`. On an offline PC, copy
the `.pt` file into `models\` and set `engine.model` to match.

**PPE analytic shows `model missing`**
Expected until a site-trained PPE model exists at `models\ppe.pt` — see
`docs/INTEGRATION.md`.

**Dashboard not reachable from another PC**
Start with `--host 0.0.0.0` and open the port in Windows Firewall. The
dashboard has no authentication; keep it on a trusted plant network or behind a
reverse proxy.

**Wrong in/out direction on a counting line**
Flip `in_direction` between `up`/`down` (or `left`/`right`) and restart.
