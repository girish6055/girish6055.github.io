"""Alert fan-out: console, generic webhook, WhatsApp Cloud API.

Delivery runs on a worker thread so a slow endpoint never stalls detection.
"""
import json
import logging
import queue
import threading
from typing import Any, Dict, List
from urllib import error, parse, request

from .config import SEVERITY_ORDER

log = logging.getLogger(__name__)


class AlertDispatcher:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config or {}
        self.min_severity = SEVERITY_ORDER.get(str(self.config.get("min_severity", "info")).lower(), 0)
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=500)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._worker, name="alert-dispatcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait({"__stop__": True})
        except queue.Full:
            pass
        self._thread.join(timeout=3)

    def send(self, event: Dict[str, Any]) -> None:
        if SEVERITY_ORDER.get(event.get("severity", "info"), 0) < self.min_severity:
            return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            log.warning("Alert queue is full; dropping alert for %s", event.get("analytic"))

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if event.get("__stop__"):
                break
            try:
                self._deliver(event)
            except Exception:  # noqa: BLE001 - a bad channel must not kill the worker
                log.exception("Alert delivery failed")

    def _deliver(self, event: Dict[str, Any]) -> None:
        if self.config.get("console", True):
            log.warning(
                "ALERT [%s] %s | %s | %s",
                event.get("severity", "info").upper(),
                event.get("camera_name"),
                event.get("title"),
                event.get("message"),
            )
        webhook = self.config.get("webhook") or {}
        if webhook.get("enabled") and webhook.get("url"):
            self._post_json(webhook["url"], event, timeout=float(webhook.get("timeout_seconds", 5)))
        whatsapp = self.config.get("whatsapp") or {}
        if whatsapp.get("enabled"):
            self._send_whatsapp(whatsapp, event)

    @staticmethod
    def _post_json(url: str, payload: Dict[str, Any], timeout: float = 5.0, headers: Dict[str, str] = None) -> None:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with request.urlopen(req, timeout=timeout) as response:
                response.read()
        except (error.URLError, error.HTTPError, OSError) as exc:
            log.error("Webhook POST to %s failed: %s", parse.urlparse(url).netloc, exc)

    def _send_whatsapp(self, cfg: Dict[str, Any], event: Dict[str, Any]) -> None:
        phone_number_id = cfg.get("phone_number_id")
        token = cfg.get("access_token")
        recipients: List[str] = cfg.get("recipients") or []
        if not (phone_number_id and token and recipients):
            log.error("WhatsApp alerts enabled but phone_number_id/access_token/recipients are not set")
            return
        text = (
            f"*INTECK Alert - {event.get('severity', 'info').upper()}*\n"
            f"{event.get('title')}\n"
            f"Camera: {event.get('camera_name')}\n"
            f"Time: {event.get('ts')}\n"
            f"{event.get('message')}"
        )
        url = f"https://graph.facebook.com/v20.0/{phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {token}"}
        for recipient in recipients:
            payload = {
                "messaging_product": "whatsapp",
                "to": recipient,
                "type": "text",
                "text": {"preview_url": False, "body": text},
            }
            self._post_json(url, payload, timeout=float(cfg.get("timeout_seconds", 8)), headers=headers)
