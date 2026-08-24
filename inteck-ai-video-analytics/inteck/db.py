"""SQLite event store. Single writer thread-safe via a lock."""
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .paths import resolve

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,
    camera_id    TEXT    NOT NULL,
    camera_name  TEXT    NOT NULL,
    analytic     TEXT    NOT NULL,
    severity     TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    message      TEXT    NOT NULL,
    zone         TEXT,
    track_ids    TEXT,
    snapshot     TEXT,
    clip         TEXT,
    meta         TEXT,
    acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_camera ON events(camera_id);
CREATE INDEX IF NOT EXISTS idx_events_analytic ON events(analytic);

CREATE TABLE IF NOT EXISTS counters (
    camera_id  TEXT NOT NULL,
    analytic   TEXT NOT NULL,
    name       TEXT NOT NULL,
    day        TEXT NOT NULL,
    value      INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (camera_id, analytic, name, day)
);
"""


class Database:
    def __init__(self, path: str = "logs/events.db") -> None:
        self.path = resolve(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def insert_event(self, event: Dict[str, Any]) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO events
                   (ts, camera_id, camera_name, analytic, severity, title, message,
                    zone, track_ids, snapshot, clip, meta)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event["ts"],
                    event["camera_id"],
                    event["camera_name"],
                    event["analytic"],
                    event["severity"],
                    event["title"],
                    event["message"],
                    event.get("zone"),
                    json.dumps(event.get("track_ids") or []),
                    event.get("snapshot"),
                    event.get("clip"),
                    json.dumps(event.get("meta") or {}),
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def list_events(
        self,
        limit: int = 100,
        offset: int = 0,
        camera_id: Optional[str] = None,
        analytic: Optional[str] = None,
        severity: Optional[str] = None,
        since: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clauses, params = [], []
        if camera_id:
            clauses.append("camera_id = ?")
            params.append(camera_id)
        if analytic:
            clauses.append("analytic = ?")
            params.append(analytic)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        if since:
            clauses.append("ts >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM events {where} ORDER BY id DESC LIMIT ? OFFSET ?", params
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def count_events(self, since: Optional[str] = None) -> Dict[str, int]:
        where, params = ("WHERE ts >= ?", [since]) if since else ("", [])
        with self._lock:
            rows = self._conn.execute(
                f"SELECT severity, COUNT(*) AS n FROM events {where} GROUP BY severity", params
            ).fetchall()
        result = {"info": 0, "warning": 0, "critical": 0}
        for row in rows:
            result[row["severity"]] = int(row["n"])
        result["total"] = sum(result.values())
        return result

    def events_by_analytic(self, since: Optional[str] = None) -> Dict[str, int]:
        where, params = ("WHERE ts >= ?", [since]) if since else ("", [])
        with self._lock:
            rows = self._conn.execute(
                f"SELECT analytic, COUNT(*) AS n FROM events {where} GROUP BY analytic ORDER BY n DESC", params
            ).fetchall()
        return {row["analytic"]: int(row["n"]) for row in rows}

    def acknowledge(self, event_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE events SET acknowledged = 1 WHERE id = ?", (event_id,))
            self._conn.commit()

    def set_counter(self, camera_id: str, analytic: str, name: str, day: str, value: int) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO counters (camera_id, analytic, name, day, value, updated_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(camera_id, analytic, name, day)
                   DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (camera_id, analytic, name, day, int(value), datetime.now().isoformat(timespec="seconds")),
            )
            self._conn.commit()

    def get_counters(self, day: Optional[str] = None) -> List[Dict[str, Any]]:
        where, params = ("WHERE day = ?", [day]) if day else ("", [])
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM counters {where} ORDER BY camera_id, analytic, name", params
            ).fetchall()
        return [dict(row) for row in rows]

    def purge_older_than(self, days: int) -> int:
        if days <= 0:
            return 0
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        with self._lock:
            cursor = self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            self._conn.commit()
            return cursor.rowcount


def _row_to_event(row: sqlite3.Row) -> Dict[str, Any]:
    event = dict(row)
    for key in ("track_ids", "meta"):
        try:
            event[key] = json.loads(event.get(key) or ("[]" if key == "track_ids" else "{}"))
        except (TypeError, ValueError):
            event[key] = [] if key == "track_ids" else {}
    return event
