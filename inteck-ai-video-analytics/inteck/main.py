"""Application entry point."""
import argparse
import logging
import signal
import sys
import threading
import time
import webbrowser

from . import APP_NAME, __version__
from .config import ConfigError, load_config
from .logging_setup import setup_logging
from .paths import app_root, ensure_dirs

log = logging.getLogger("inteck.main")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="INTECK_AI_Analytics", description=APP_NAME)
    parser.add_argument("--config", default="config/config.json", help="path to config.json")
    parser.add_argument("--host", default=None, help="dashboard bind address")
    parser.add_argument("--port", type=int, default=None, help="dashboard port")
    parser.add_argument("--no-browser", action="store_true", help="do not open the dashboard automatically")
    parser.add_argument("--no-dashboard", action="store_true", help="run analytics headless")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--check-config", action="store_true", help="validate configuration and exit")
    parser.add_argument("--list-analytics", action="store_true", help="list available analytic types and exit")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    ensure_dirs()

    if args.list_analytics:
        from .analytics import REGISTRY

        for name, cls in sorted(REGISTRY.items()):
            print(f"{name:20s} {cls.title}")
        return 0

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    setup_logging(config.storage.get("log_file", "logs/inteck.log"), args.log_level)
    log.info("%s %s starting", APP_NAME, __version__)
    log.info("Working directory: %s", app_root())
    log.info("Configuration: %s", config.path)

    if args.check_config:
        from .analytics import REGISTRY

        problems = _validate(config, REGISTRY)
        for problem in problems:
            log.error("%s", problem)
        if problems:
            print(f"\n{len(problems)} configuration problem(s) found.", file=sys.stderr)
            return 1
        cameras = sum(1 for camera in config.cameras if camera.enabled)
        analytics = sum(len(camera.analytics) for camera in config.cameras if camera.enabled)
        print(f"Configuration OK: {cameras} camera(s), {analytics} analytic(s).")
        return 0

    from .engine import AnalyticsEngine

    engine = AnalyticsEngine(config)
    try:
        engine.start()
    except Exception as exc:  # noqa: BLE001
        log.error("Engine failed to start: %s", exc)
        return 3

    stop_event = threading.Event()

    def handle_signal(signum, _frame):
        log.info("Signal %s received, shutting down", signum)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handle_signal)
        except (ValueError, OSError):  # pragma: no cover - non-main thread / Windows service
            pass

    if args.no_dashboard:
        log.info("Dashboard disabled; running headless. Press Ctrl+C to stop.")
        try:
            while not stop_event.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        engine.stop()
        return 0

    from .web.app import create_app

    app = create_app(engine)
    host = args.host or config.dashboard.get("host", "127.0.0.1")
    port = int(args.port or config.dashboard.get("port", 8080))
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    log.info("Dashboard: %s", url)

    if config.dashboard.get("open_browser", True) and not args.no_browser:
        threading.Timer(1.5, lambda: _open_browser(url)).start()

    try:
        app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Stopping engine")
        engine.stop()
    return 0


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        log.debug("Could not open a browser automatically")


def _validate(config, registry) -> list:
    problems = []
    for camera in config.cameras:
        if not camera.enabled:
            continue
        if "username:password" in camera.source or "192.168.1.10" in camera.source:
            problems.append(f"Camera {camera.id}: source still holds the example RTSP URL - set your real camera URL.")
        for entry in camera.analytics:
            if entry.type not in registry:
                problems.append(f"Camera {camera.id}: unknown analytic type '{entry.type}'.")
                continue
            zone = entry.options.get("zone")
            if zone and zone not in camera.zones:
                problems.append(f"Camera {camera.id}/{entry.type}: zone '{zone}' is not defined for this camera.")
            line = entry.options.get("line")
            if line and line not in camera.lines:
                problems.append(f"Camera {camera.id}/{entry.type}: line '{line}' is not defined for this camera.")
            if entry.type in ("people_counting", "vehicle_counting", "door_tailgating") and not line:
                problems.append(f"Camera {camera.id}/{entry.type}: a 'line' is required.")
    if not any(camera.enabled for camera in config.cameras):
        problems.append("No cameras are enabled in config.json.")
    return problems


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
