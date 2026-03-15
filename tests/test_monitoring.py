"""
test_monitoring.py - Unit tests for PC3 components (primary DB, monitoring, health check).

Tests:
  - DB primary envelope processing (all 4 types + unknown)
  - Monitoring query building for each command type
  - Response pretty-print formatters produce output without errors
  - Health check PING/PONG via ZMQ REP socket
  - ZMQ integration: REQ/REP round-trip for monitoring queries
"""

import json
import os
import tempfile
import time

import zmq

from common.constants import (
    CMD_FORCE_GREEN_WAVE,
    CMD_FORCE_SEMAPHORE,
    CMD_HEALTH_CHECK,
    CMD_QUERY_HISTORY,
    CMD_QUERY_INTERSECTION,
    CMD_SYSTEM_STATUS,
    HEALTH_CHECK_RESPONSE,
)
from common.db_utils import TrafficDB
from common.models import MonitoringQuery, MonitoringResponse
from common.monitoring_commands import (
    format_green_wave_response,
    format_health_check_response,
    format_history_response,
    format_intersection_response,
    format_semaphore_response,
    format_system_status_response,
)
from pc3.db_primary import process_envelope

# =============================================================================
# Helpers
# =============================================================================


def _make_db() -> TrafficDB:
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return TrafficDB(path)


# =============================================================================
# DB Primary Envelope Processing Tests
# =============================================================================


class TestDBPrimaryProcessing:
    """Tests for DB primary envelope processing (mirrors TestDBReplicaProcessing)."""

    def test_process_sensor_event_envelope(self):
        """Should insert sensor event into primary DB from envelope."""
        db = _make_db()
        try:
            envelope = {
                "type": "sensor_event",
                "data": {
                    "sensor_id": "CAM-A1",
                    "tipo_sensor": "camara",
                    "interseccion": "INT-A1",
                    "event_data": {"volumen": 10},
                    "timestamp": "2026-01-01T10:00:00Z",
                },
            }
            process_envelope(db, envelope)
            events = db.query_events_by_intersection("INT-A1")
            assert len(events) == 1
            assert events[0]["sensor_id"] == "CAM-A1"
        finally:
            db.close()

    def test_process_congestion_envelope(self):
        """Should insert congestion record into primary DB from envelope."""
        db = _make_db()
        try:
            envelope = {
                "type": "congestion_record",
                "data": {
                    "interseccion": "INT-B2",
                    "traffic_state": "CONGESTION",
                    "decision": "EXTEND_GREEN",
                    "details": "Q=12, Vp=18",
                    "sensor_data": {"Q": 12},
                    "timestamp": "2026-01-01T10:00:00Z",
                },
            }
            process_envelope(db, envelope)
            records = db.query_congestion_history(interseccion="INT-B2")
            assert len(records) == 1
            assert records[0]["traffic_state"] == "CONGESTION"
        finally:
            db.close()

    def test_process_semaphore_state_envelope(self):
        """Should insert semaphore state into primary DB from envelope."""
        db = _make_db()
        try:
            envelope = {
                "type": "semaphore_state",
                "data": {
                    "interseccion": "INT-C3",
                    "state_ns": "GREEN",
                    "state_ew": "RED",
                    "reason": "congestion",
                    "cycle_duration_sec": 25,
                    "timestamp": "2026-01-01T10:00:00Z",
                },
            }
            process_envelope(db, envelope)
            state = db.query_semaphore_state("INT-C3")
            assert state is not None
            assert state["state_ns"] == "GREEN"
            assert state["state_ew"] == "RED"
        finally:
            db.close()

    def test_process_priority_action_envelope(self):
        """Should insert priority action into primary DB from envelope."""
        db = _make_db()
        try:
            envelope = {
                "type": "priority_action",
                "data": {
                    "action_type": "GREEN_WAVE",
                    "target": "row_B",
                    "reason": "ambulance",
                    "requested_by": "user",
                    "affected_intersections": ["INT-B1", "INT-B2"],
                    "timestamp": "2026-01-01T10:00:00Z",
                },
            }
            process_envelope(db, envelope)
            actions = db.query_priority_actions()
            assert len(actions) == 1
            assert actions[0]["action_type"] == "GREEN_WAVE"
        finally:
            db.close()

    def test_process_unknown_type_no_error(self):
        """Unknown envelope type should log warning but not raise."""
        db = _make_db()
        try:
            envelope = {"type": "unknown_type", "data": {}}
            process_envelope(db, envelope)
        finally:
            db.close()


# =============================================================================
# Monitoring Query Building Tests
# =============================================================================


class TestMonitoringQueryBuilding:
    """Tests that MonitoringQuery instances are built correctly for each command."""

    def test_query_intersection(self):
        """QUERY_INTERSECTION should include the interseccion field."""
        query = MonitoringQuery(
            command=CMD_QUERY_INTERSECTION,
            interseccion="INT-A1",
        )
        data = json.loads(query.to_json())
        assert data["command"] == CMD_QUERY_INTERSECTION
        assert data["interseccion"] == "INT-A1"

    def test_query_history_full(self):
        """QUERY_HISTORY with all parameters should serialize correctly."""
        query = MonitoringQuery(
            command=CMD_QUERY_HISTORY,
            interseccion="INT-B2",
            timestamp_inicio="2026-01-01T10:00:00Z",
            timestamp_fin="2026-01-01T12:00:00Z",
        )
        data = json.loads(query.to_json())
        assert data["command"] == CMD_QUERY_HISTORY
        assert data["interseccion"] == "INT-B2"
        assert data["timestamp_inicio"] == "2026-01-01T10:00:00Z"
        assert data["timestamp_fin"] == "2026-01-01T12:00:00Z"

    def test_query_history_no_filters(self):
        """QUERY_HISTORY without filters should have None fields."""
        query = MonitoringQuery(command=CMD_QUERY_HISTORY)
        data = json.loads(query.to_json())
        assert data["command"] == CMD_QUERY_HISTORY
        assert data["interseccion"] is None
        assert data["timestamp_inicio"] is None

    def test_force_green_wave_row(self):
        """FORCE_GREEN_WAVE by row should include row field."""
        query = MonitoringQuery(
            command=CMD_FORCE_GREEN_WAVE,
            row="B",
            reason="ambulance",
        )
        data = json.loads(query.to_json())
        assert data["command"] == CMD_FORCE_GREEN_WAVE
        assert data["row"] == "B"
        assert data["column"] is None
        assert data["reason"] == "ambulance"

    def test_force_green_wave_column(self):
        """FORCE_GREEN_WAVE by column should include column field."""
        query = MonitoringQuery(
            command=CMD_FORCE_GREEN_WAVE,
            column=3,
            reason="emergency",
        )
        data = json.loads(query.to_json())
        assert data["command"] == CMD_FORCE_GREEN_WAVE
        assert data["row"] is None
        assert data["column"] == 3

    def test_force_semaphore(self):
        """FORCE_SEMAPHORE should include interseccion, new_state, reason."""
        query = MonitoringQuery(
            command=CMD_FORCE_SEMAPHORE,
            interseccion="INT-C2",
            new_state="GREEN",
            reason="manual override",
        )
        data = json.loads(query.to_json())
        assert data["command"] == CMD_FORCE_SEMAPHORE
        assert data["interseccion"] == "INT-C2"
        assert data["new_state"] == "GREEN"

    def test_system_status(self):
        """SYSTEM_STATUS needs only the command field."""
        query = MonitoringQuery(command=CMD_SYSTEM_STATUS)
        data = json.loads(query.to_json())
        assert data["command"] == CMD_SYSTEM_STATUS

    def test_health_check(self):
        """HEALTH_CHECK needs only the command field."""
        query = MonitoringQuery(command=CMD_HEALTH_CHECK)
        data = json.loads(query.to_json())
        assert data["command"] == CMD_HEALTH_CHECK


# =============================================================================
# Response Formatting Tests
# =============================================================================


class TestResponseFormatting:
    """Tests that response formatters produce output without errors."""

    def test_format_intersection_response_ok(self, capsys):
        """Intersection formatter should print readings and semaphore state."""
        resp = MonitoringResponse(
            status="OK",
            command=CMD_QUERY_INTERSECTION,
            message="Current state of INT-A1",
            data={
                "intersection": "INT-A1",
                "current_readings": {
                    "Q": 3,
                    "Vp": 42.5,
                    "D": 8,
                    "traffic_state": "NORMAL",
                    "last_updated": "2026-01-01T10:00:00Z",
                },
                "semaphore_state": {
                    "state_ns": "RED",
                    "state_ew": "GREEN",
                    "cycle_duration_sec": 15,
                },
                "recent_events_count": 5,
            },
        )
        format_intersection_response(resp)
        captured = capsys.readouterr()
        assert "INT-A1" in captured.out
        assert "NORMAL" in captured.out
        assert "42.5" in captured.out

    def test_format_intersection_response_error(self, capsys):
        """Intersection formatter should handle ERROR status."""
        resp = MonitoringResponse(
            status="ERROR",
            command=CMD_QUERY_INTERSECTION,
            message="Intersection not found: INT-Z9",
        )
        format_intersection_response(resp)
        captured = capsys.readouterr()
        assert "ERROR" in captured.out

    def test_format_history_response(self, capsys):
        """History formatter should list congestion records."""
        resp = MonitoringResponse(
            status="OK",
            command=CMD_QUERY_HISTORY,
            message="Found 2 records",
            data={
                "records": [
                    {
                        "timestamp": "2026-01-01T11:00:00Z",
                        "interseccion": "INT-B2",
                        "traffic_state": "CONGESTION",
                        "decision": "EXTEND_GREEN",
                        "details": "Q=12",
                    },
                    {
                        "timestamp": "2026-01-01T10:30:00Z",
                        "interseccion": "INT-C3",
                        "traffic_state": "NORMAL",
                        "decision": "NO_ACTION",
                        "details": "Q=2",
                    },
                ]
            },
        )
        format_history_response(resp)
        captured = capsys.readouterr()
        assert "2 records" in captured.out
        assert "INT-B2" in captured.out
        assert "INT-C3" in captured.out

    def test_format_history_response_empty(self, capsys):
        """History formatter should handle empty records."""
        resp = MonitoringResponse(
            status="OK",
            command=CMD_QUERY_HISTORY,
            message="Found 0 records",
            data={"records": []},
        )
        format_history_response(resp)
        captured = capsys.readouterr()
        assert "no records found" in captured.out

    def test_format_green_wave_response(self, capsys):
        """Green wave formatter should list affected intersections."""
        resp = MonitoringResponse(
            status="OK",
            command=CMD_FORCE_GREEN_WAVE,
            message="Green wave activated",
            data={
                "affected": ["INT-B1", "INT-B2", "INT-B3", "INT-B4"],
                "duration_sec": 30,
            },
        )
        format_green_wave_response(resp)
        captured = capsys.readouterr()
        assert "INT-B1" in captured.out
        assert "30" in captured.out

    def test_format_semaphore_response(self, capsys):
        """Semaphore formatter should show intersection and new state."""
        resp = MonitoringResponse(
            status="OK",
            command=CMD_FORCE_SEMAPHORE,
            message="Forced",
            data={"intersection": "INT-C2", "new_state": "GREEN"},
        )
        format_semaphore_response(resp)
        captured = capsys.readouterr()
        assert "INT-C2" in captured.out
        assert "GREEN" in captured.out

    def test_format_system_status_response(self, capsys):
        """System status formatter should show summary counts."""
        resp = MonitoringResponse(
            status="OK",
            command=CMD_SYSTEM_STATUS,
            message="System status",
            data={
                "db_summary": {
                    "total_sensor_events": 1520,
                    "total_congestion_detections": 87,
                    "total_green_waves": 3,
                    "total_semaphore_changes": 245,
                },
                "currently_congested": ["INT-B2", "INT-C3"],
                "currently_green_wave": [],
                "total_intersections": 16,
            },
        )
        format_system_status_response(resp)
        captured = capsys.readouterr()
        assert "1520" in captured.out
        assert "87" in captured.out
        assert "INT-B2" in captured.out
        assert "(none)" in captured.out  # green wave is empty

    def test_format_health_check_ok(self, capsys):
        """Health check formatter should show OK status."""
        resp = MonitoringResponse(
            status="OK",
            command=CMD_HEALTH_CHECK,
            message="PONG",
        )
        format_health_check_response(resp)
        captured = capsys.readouterr()
        assert "OK" in captured.out
        assert "PONG" in captured.out

    def test_format_health_check_fail(self, capsys):
        """Health check formatter should show FAILED status."""
        resp = MonitoringResponse(
            status="ERROR",
            command=CMD_HEALTH_CHECK,
            message="timeout",
        )
        format_health_check_response(resp)
        captured = capsys.readouterr()
        assert "FAILED" in captured.out


# =============================================================================
# Health Check PING/PONG Tests
# =============================================================================


class TestHealthCheck:
    """Tests for the health check REP socket (PING/PONG)."""

    def test_health_check_ping_pong(self):
        """Health check REP should respond PONG to PING."""
        context = zmq.Context()
        port = 17565

        # REP socket (simulating db_primary health check thread)
        rep = context.socket(zmq.REP)
        rep.bind(f"tcp://127.0.0.1:{port}")

        # REQ socket (simulating PC2 health checker)
        req = context.socket(zmq.REQ)
        req.connect(f"tcp://127.0.0.1:{port}")

        time.sleep(0.3)

        # Send PING
        req.send_string("PING")

        # Receive at REP
        rep.setsockopt(zmq.RCVTIMEO, 2000)
        received = rep.recv_string()
        assert received == "PING"

        # Reply PONG
        rep.send_string(HEALTH_CHECK_RESPONSE)

        # Receive PONG at REQ
        req.setsockopt(zmq.RCVTIMEO, 2000)
        response = req.recv_string()
        assert response == "PONG"

        rep.close()
        req.close()
        context.term()


# =============================================================================
# ZMQ Integration Tests
# =============================================================================


class TestMonitoringZMQIntegration:
    """Integration tests for monitoring REQ/REP round-trip."""

    def test_query_round_trip(self):
        """
        Full round-trip: monitoring sends REQ query, mock analytics
        responds via REP, monitoring receives the response.
        """
        context = zmq.Context()
        port = 17561

        # REP socket (simulating analytics service on PC2)
        rep = context.socket(zmq.REP)
        rep.bind(f"tcp://127.0.0.1:{port}")

        # REQ socket (simulating monitoring on PC3)
        req = context.socket(zmq.REQ)
        req.connect(f"tcp://127.0.0.1:{port}")

        time.sleep(0.3)

        # Monitoring sends SYSTEM_STATUS query
        query = MonitoringQuery(command=CMD_SYSTEM_STATUS)
        req.send_string(query.to_json())

        # Analytics receives and responds
        rep.setsockopt(zmq.RCVTIMEO, 2000)
        received = rep.recv_string()
        query_data = json.loads(received)
        assert query_data["command"] == CMD_SYSTEM_STATUS

        response = MonitoringResponse(
            status="OK",
            command=CMD_SYSTEM_STATUS,
            message="All systems nominal",
            data={
                "db_summary": {
                    "total_sensor_events": 100,
                    "total_congestion_detections": 5,
                    "total_green_waves": 1,
                    "total_semaphore_changes": 20,
                },
                "currently_congested": [],
                "currently_green_wave": [],
                "total_intersections": 16,
            },
        )
        rep.send_string(response.to_json())

        # Monitoring receives response
        req.setsockopt(zmq.RCVTIMEO, 2000)
        resp_str = req.recv_string()
        resp_data = json.loads(resp_str)
        assert resp_data["status"] == "OK"
        assert resp_data["data"]["total_intersections"] == 16

        rep.close()
        req.close()
        context.term()

    def test_force_green_wave_round_trip(self):
        """
        Round-trip for FORCE_GREEN_WAVE: monitoring sends, analytics responds.
        """
        context = zmq.Context()
        port = 17562

        rep = context.socket(zmq.REP)
        rep.bind(f"tcp://127.0.0.1:{port}")

        req = context.socket(zmq.REQ)
        req.connect(f"tcp://127.0.0.1:{port}")

        time.sleep(0.3)

        # Send green wave request
        query = MonitoringQuery(
            command=CMD_FORCE_GREEN_WAVE,
            row="B",
            reason="ambulance",
        )
        req.send_string(query.to_json())

        # Analytics receives
        rep.setsockopt(zmq.RCVTIMEO, 2000)
        received = rep.recv_string()
        query_data = json.loads(received)
        assert query_data["command"] == CMD_FORCE_GREEN_WAVE
        assert query_data["row"] == "B"
        assert query_data["reason"] == "ambulance"

        # Analytics responds
        response = MonitoringResponse(
            status="OK",
            command=CMD_FORCE_GREEN_WAVE,
            message="Green wave activated on row_B",
            data={
                "affected": ["INT-B1", "INT-B2", "INT-B3", "INT-B4"],
                "duration_sec": 30,
            },
        )
        rep.send_string(response.to_json())

        # Monitoring receives
        req.setsockopt(zmq.RCVTIMEO, 2000)
        resp_str = req.recv_string()
        resp_data = json.loads(resp_str)
        assert resp_data["status"] == "OK"
        assert len(resp_data["data"]["affected"]) == 4

        rep.close()
        req.close()
        context.term()

    def test_db_primary_push_pull(self):
        """
        Simulate analytics PUSH -> db_primary PULL for envelope delivery.
        """
        context = zmq.Context()
        port = 17563

        # PULL socket (simulating db_primary)
        pull = context.socket(zmq.PULL)
        pull.bind(f"tcp://127.0.0.1:{port}")

        # PUSH socket (simulating analytics)
        push = context.socket(zmq.PUSH)
        push.connect(f"tcp://127.0.0.1:{port}")

        time.sleep(0.3)

        # Send sensor event envelope
        envelope = {
            "type": "sensor_event",
            "data": {
                "sensor_id": "GPS-D2",
                "tipo_sensor": "gps",
                "interseccion": "INT-D2",
                "event_data": {"velocidad_promedio": 12.5},
                "timestamp": "2026-01-01T10:00:00Z",
            },
        }
        push.send_string(json.dumps(envelope))

        # Receive and verify
        pull.setsockopt(zmq.RCVTIMEO, 2000)
        received = pull.recv_string()
        data = json.loads(received)

        assert data["type"] == "sensor_event"
        assert data["data"]["sensor_id"] == "GPS-D2"

        pull.close()
        push.close()
        context.term()
