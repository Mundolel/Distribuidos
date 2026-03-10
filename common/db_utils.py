"""
db_utils.py - SQLite database utilities for the traffic management system.

Provides functions to create the schema, insert sensor events, semaphore
state changes, analytics decisions, and query historical data. Used by
both the primary DB (PC3) and the replica DB (PC2).
"""

import json
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Schema Definition
# =============================================================================

SCHEMA_SQL = """
-- Sensor events table: stores all raw events from cameras, inductive loops, GPS
CREATE TABLE IF NOT EXISTS sensor_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sensor_id TEXT NOT NULL,
    tipo_sensor TEXT NOT NULL,
    interseccion TEXT NOT NULL,
    event_data TEXT NOT NULL,           -- Full event JSON
    timestamp TEXT NOT NULL,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Index for querying by intersection and time range
CREATE INDEX IF NOT EXISTS idx_sensor_events_intersection
    ON sensor_events(interseccion);
CREATE INDEX IF NOT EXISTS idx_sensor_events_timestamp
    ON sensor_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_sensor_events_tipo
    ON sensor_events(tipo_sensor);

-- Semaphore state changes: tracks every light change
CREATE TABLE IF NOT EXISTS semaphore_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    interseccion TEXT NOT NULL,
    state_ns TEXT NOT NULL,             -- North-South direction: GREEN or RED
    state_ew TEXT NOT NULL,             -- East-West direction: GREEN or RED
    reason TEXT DEFAULT '',
    cycle_duration_sec INTEGER DEFAULT 15,
    timestamp TEXT NOT NULL,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_semaphore_states_intersection
    ON semaphore_states(interseccion);
CREATE INDEX IF NOT EXISTS idx_semaphore_states_timestamp
    ON semaphore_states(timestamp);

-- Congestion history: records detected congestion events
CREATE TABLE IF NOT EXISTS congestion_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    interseccion TEXT NOT NULL,
    traffic_state TEXT NOT NULL,        -- NORMAL, CONGESTION, GREEN_WAVE
    decision TEXT NOT NULL,             -- NO_ACTION, EXTEND_GREEN, GREEN_WAVE, FORCE_CHANGE
    details TEXT DEFAULT '',
    sensor_data TEXT DEFAULT '',        -- JSON snapshot of sensor readings
    timestamp TEXT NOT NULL,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_congestion_history_intersection
    ON congestion_history(interseccion);
CREATE INDEX IF NOT EXISTS idx_congestion_history_timestamp
    ON congestion_history(timestamp);
CREATE INDEX IF NOT EXISTS idx_congestion_history_state
    ON congestion_history(traffic_state);

-- Priority actions: records green wave / forced changes by users
CREATE TABLE IF NOT EXISTS priority_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL,          -- GREEN_WAVE, FORCE_CHANGE
    target TEXT NOT NULL,               -- Row, column, or specific intersection
    reason TEXT DEFAULT '',
    requested_by TEXT DEFAULT 'system',
    affected_intersections TEXT DEFAULT '',  -- JSON list of affected intersections
    timestamp TEXT NOT NULL,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_priority_actions_timestamp
    ON priority_actions(timestamp);
"""


# =============================================================================
# Database Connection Management
# =============================================================================


class TrafficDB:
    """
    SQLite database wrapper for the traffic management system.
    Handles connection management, schema creation, and all CRUD operations.
    """

    def __init__(self, db_path: str):
        """
        Initialize the database connection and create schema if needed.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row  # Return rows as dict-like objects
        self.conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent access
        self._create_schema()
        logger.info(f"Database initialized at {db_path}")

    def _create_schema(self):
        """Create all tables and indices if they don't exist."""
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            logger.info(f"Database closed: {self.db_path}")

    # =========================================================================
    # Insert Operations
    # =========================================================================

    def insert_sensor_event(
        self, sensor_id: str, tipo_sensor: str, interseccion: str, event_data: dict, timestamp: str
    ) -> int:
        """
        Insert a raw sensor event into the database.

        Args:
            sensor_id: Sensor identifier (e.g., "CAM-A1").
            tipo_sensor: Sensor type (e.g., "camara").
            interseccion: Intersection ID (e.g., "INT-A1").
            event_data: Full event as a dictionary.
            timestamp: Event timestamp in ISO format.

        Returns:
            The row ID of the inserted record.
        """
        cursor = self.conn.execute(
            """INSERT INTO sensor_events
               (sensor_id, tipo_sensor, interseccion, event_data, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (
                sensor_id,
                tipo_sensor,
                interseccion,
                json.dumps(event_data, ensure_ascii=False),
                timestamp,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def insert_semaphore_state(
        self,
        interseccion: str,
        state_ns: str,
        state_ew: str,
        reason: str,
        cycle_duration_sec: int,
        timestamp: str,
    ) -> int:
        """
        Record a semaphore state change.

        Returns:
            The row ID of the inserted record.
        """
        cursor = self.conn.execute(
            """INSERT INTO semaphore_states
               (interseccion, state_ns, state_ew, reason, cycle_duration_sec, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (interseccion, state_ns, state_ew, reason, cycle_duration_sec, timestamp),
        )
        self.conn.commit()
        return cursor.lastrowid

    def insert_congestion_record(
        self,
        interseccion: str,
        traffic_state: str,
        decision: str,
        details: str,
        sensor_data: dict | None,
        timestamp: str,
    ) -> int:
        """
        Record a congestion detection event and the analytics decision.

        Returns:
            The row ID of the inserted record.
        """
        sensor_json = json.dumps(sensor_data, ensure_ascii=False) if sensor_data else ""
        cursor = self.conn.execute(
            """INSERT INTO congestion_history
               (interseccion, traffic_state, decision, details, sensor_data, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (interseccion, traffic_state, decision, details, sensor_json, timestamp),
        )
        self.conn.commit()
        return cursor.lastrowid

    def insert_priority_action(
        self,
        action_type: str,
        target: str,
        reason: str,
        requested_by: str,
        affected_intersections: list[str],
        timestamp: str,
    ) -> int:
        """
        Record a priority action (green wave, forced change).

        Returns:
            The row ID of the inserted record.
        """
        affected_json = json.dumps(affected_intersections)
        cursor = self.conn.execute(
            """INSERT INTO priority_actions
               (action_type, target, reason, requested_by,
                affected_intersections, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (action_type, target, reason, requested_by, affected_json, timestamp),
        )
        self.conn.commit()
        return cursor.lastrowid

    # =========================================================================
    # Query Operations
    # =========================================================================

    def query_events_by_intersection(
        self, interseccion: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """
        Get recent sensor events for a specific intersection.

        Args:
            interseccion: Intersection ID (e.g., "INT-A1").
            limit: Maximum number of results.

        Returns:
            List of event dictionaries.
        """
        cursor = self.conn.execute(
            """SELECT * FROM sensor_events
               WHERE interseccion = ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (interseccion, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_events_by_time_range(
        self,
        timestamp_inicio: str,
        timestamp_fin: str,
        interseccion: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """
        Get sensor events within a time range, optionally filtered by intersection.

        Args:
            timestamp_inicio: Start timestamp (ISO format).
            timestamp_fin: End timestamp (ISO format).
            interseccion: Optional intersection filter.
            limit: Maximum number of results.

        Returns:
            List of event dictionaries.
        """
        if interseccion:
            cursor = self.conn.execute(
                """SELECT * FROM sensor_events
                   WHERE timestamp >= ? AND timestamp <= ?
                   AND interseccion = ?
                   ORDER BY timestamp DESC
                   LIMIT ?""",
                (timestamp_inicio, timestamp_fin, interseccion, limit),
            )
        else:
            cursor = self.conn.execute(
                """SELECT * FROM sensor_events
                   WHERE timestamp >= ? AND timestamp <= ?
                   ORDER BY timestamp DESC
                   LIMIT ?""",
                (timestamp_inicio, timestamp_fin, limit),
            )
        return [dict(row) for row in cursor.fetchall()]

    def query_congestion_history(
        self,
        timestamp_inicio: str | None = None,
        timestamp_fin: str | None = None,
        interseccion: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Query congestion history with optional filters.

        Returns:
            List of congestion record dictionaries.
        """
        query = "SELECT * FROM congestion_history WHERE 1=1"
        params = []

        if timestamp_inicio:
            query += " AND timestamp >= ?"
            params.append(timestamp_inicio)
        if timestamp_fin:
            query += " AND timestamp <= ?"
            params.append(timestamp_fin)
        if interseccion:
            query += " AND interseccion = ?"
            params.append(interseccion)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = self.conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def query_semaphore_state(self, interseccion: str) -> dict[str, Any] | None:
        """
        Get the most recent semaphore state for an intersection.

        Returns:
            Dictionary with latest state, or None if no records.
        """
        cursor = self.conn.execute(
            """SELECT * FROM semaphore_states
               WHERE interseccion = ?
               ORDER BY timestamp DESC
               LIMIT 1""",
            (interseccion,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def query_all_semaphore_states(self) -> list[dict[str, Any]]:
        """
        Get the latest state of every semaphore (one per intersection).

        Returns:
            List of the most recent state for each intersection.
        """
        cursor = self.conn.execute(
            """SELECT s1.* FROM semaphore_states s1
               INNER JOIN (
                   SELECT interseccion, MAX(timestamp) as max_ts
                   FROM semaphore_states
                   GROUP BY interseccion
               ) s2 ON s1.interseccion = s2.interseccion
               AND s1.timestamp = s2.max_ts
               ORDER BY s1.interseccion"""
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_priority_actions(self, limit: int = 50) -> list[dict[str, Any]]:
        """
        Get recent priority actions (green waves, forced changes).

        Returns:
            List of priority action dictionaries.
        """
        cursor = self.conn.execute(
            """SELECT * FROM priority_actions
               ORDER BY timestamp DESC
               LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_event_count_in_interval(self, timestamp_inicio: str, timestamp_fin: str) -> int:
        """
        Count the number of sensor events stored in a time interval.
        Used for performance metrics (throughput measurement).

        Returns:
            Number of events in the interval.
        """
        cursor = self.conn.execute(
            """SELECT COUNT(*) as cnt FROM sensor_events
               WHERE timestamp >= ? AND timestamp <= ?""",
            (timestamp_inicio, timestamp_fin),
        )
        row = cursor.fetchone()
        return row["cnt"] if row else 0

    def get_system_summary(self) -> dict[str, Any]:
        """
        Get a summary of the system state for monitoring display.

        Returns:
            Dictionary with counts and recent activity.
        """
        total_events = self.conn.execute("SELECT COUNT(*) as cnt FROM sensor_events").fetchone()[
            "cnt"
        ]

        total_congestion = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM congestion_history WHERE traffic_state = 'CONGESTION'"
        ).fetchone()["cnt"]

        total_green_waves = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM priority_actions WHERE action_type = 'GREEN_WAVE'"
        ).fetchone()["cnt"]

        total_semaphore_changes = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM semaphore_states"
        ).fetchone()["cnt"]

        return {
            "total_sensor_events": total_events,
            "total_congestion_detections": total_congestion,
            "total_green_waves": total_green_waves,
            "total_semaphore_changes": total_semaphore_changes,
        }
