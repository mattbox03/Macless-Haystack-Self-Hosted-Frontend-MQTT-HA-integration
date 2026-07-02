"""SQLite-backed append-only position event store."""

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path


class EventStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_db(self):
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS position_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    device_id TEXT NOT NULL,
                    tracker_id TEXT NOT NULL,
                    source TEXT NOT NULL CHECK(source IN ('apple', 'google')),
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    accuracy REAL NOT NULL DEFAULT 0,
                    timestamp INTEGER NOT NULL,
                    received_at INTEGER NOT NULL,
                    altitude REAL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_events_device_time
                    ON position_events(device_id, timestamp);
                CREATE INDEX IF NOT EXISTS idx_events_device_source_time
                    ON position_events(device_id, source, timestamp);
                CREATE INDEX IF NOT EXISTS idx_events_received
                    ON position_events(received_at);
                """
            )

    @staticmethod
    def _fingerprint(event):
        stable = "|".join(
            [
                str(event["device_id"]),
                str(event["tracker_id"]),
                str(event["source"]),
                str(int(event["timestamp"])),
                f"{float(event['latitude']):.7f}",
                f"{float(event['longitude']):.7f}",
            ]
        )
        return hashlib.sha256(stable.encode()).hexdigest()

    def append(self, events):
        inserted = []
        if not events:
            return inserted
        with self._lock, self._connect() as db:
            for event in events:
                normalized = {
                    "device_id": str(event["device_id"]),
                    "tracker_id": str(event.get("tracker_id") or event["device_id"]),
                    "source": str(event["source"]).lower(),
                    "latitude": float(event["latitude"]),
                    "longitude": float(event["longitude"]),
                    "accuracy": float(event.get("accuracy") or 0),
                    "timestamp": int(event["timestamp"]),
                    "received_at": int(event["received_at"]),
                    "altitude": (
                        float(event["altitude"])
                        if event.get("altitude") is not None
                        else None
                    ),
                    "metadata": event.get("metadata") or {},
                }
                fingerprint = self._fingerprint(normalized)
                cursor = db.execute(
                    """
                    INSERT OR IGNORE INTO position_events (
                        fingerprint, device_id, tracker_id, source,
                        latitude, longitude, accuracy, timestamp,
                        received_at, altitude, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fingerprint,
                        normalized["device_id"],
                        normalized["tracker_id"],
                        normalized["source"],
                        normalized["latitude"],
                        normalized["longitude"],
                        normalized["accuracy"],
                        normalized["timestamp"],
                        normalized["received_at"],
                        normalized["altitude"],
                        json.dumps(normalized["metadata"], separators=(",", ":")),
                    ),
                )
                if cursor.rowcount:
                    normalized["fingerprint"] = fingerprint
                    inserted.append(normalized)
        return inserted

    def list(self, device_id=None, source=None, limit=10000):
        clauses = []
        params = []
        if device_id:
            clauses.append("device_id = ?")
            params.append(str(device_id))
        if source and source in ("apple", "google"):
            clauses.append("source = ?")
            params.append(source)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 50000)))
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT * FROM (
                    SELECT * FROM position_events
                    {where}
                    ORDER BY timestamp DESC, received_at DESC, id DESC
                    LIMIT ?
                )
                ORDER BY timestamp ASC, received_at ASC, id ASC
                """,
                params,
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def latest(self, device_id, sources=None):
        clauses = ["device_id = ?"]
        params = [str(device_id)]
        if sources:
            placeholders = ",".join("?" for _ in sources)
            clauses.append(f"source IN ({placeholders})")
            params.extend(sources)
        with self._connect() as db:
            row = db.execute(
                f"""
                SELECT * FROM position_events
                WHERE {' AND '.join(clauses)}
                ORDER BY timestamp DESC, received_at DESC, id DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
        return self._row_to_event(row) if row else None

    def prune(self, received_before):
        with self._lock, self._connect() as db:
            return db.execute(
                "DELETE FROM position_events WHERE received_at < ?",
                (int(received_before),),
            ).rowcount

    def delete_device(self, device_id):
        with self._lock, self._connect() as db:
            return db.execute(
                "DELETE FROM position_events WHERE device_id = ?",
                (str(device_id),),
            ).rowcount

    @staticmethod
    def _row_to_event(row):
        metadata = json.loads(row["metadata"] or "{}")
        return {
            "event_id": row["id"],
            "device_id": row["device_id"],
            "tracker_id": row["tracker_id"],
            "source": row["source"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "accuracy": row["accuracy"],
            "timestamp": row["timestamp"],
            "received_at": row["received_at"],
            "altitude": row["altitude"],
            "metadata": metadata,
            # Compatibility aliases used by the existing map UI.
            "lat": row["latitude"],
            "lon": row["longitude"],
            "acc": row["accuracy"],
            "ts": row["timestamp"],
        }
