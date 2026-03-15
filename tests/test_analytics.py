"""
test_analytics.py - Unit tests for PC2 analytics, semaphore control, and DB replica.

Tests:
  - Traffic rule evaluation (normal, congestion, edge cases)
  - Per-intersection state update from sensor events
  - DB envelope creation and parsing
  - Semaphore control command application
  - DB replica envelope processing
  - Monitoring query handling
  - ZMQ integration: sensor -> analytics -> semaphore command chain
"""

import json
import os
import tempfile
import time

import zmq

from common.config_loader import CityConfig
from common.constants import (
    DECISION_EXTEND_GREEN,
    DECISION_NO_ACTION,
    SEMAPHORE_GREEN,
    SEMAPHORE_RED,
    TOPIC_CAMERA,
    TOPIC_GPS,
    TOPIC_INDUCTIVE,
    TRAFFIC_CONGESTION,
    TRAFFIC_NORMAL,
)
from common.db_utils import TrafficDB
from common.models import (
    MonitoringQuery,
    SemaphoreCommand,
)
from pc2.analytics_service import (
    evaluate_traffic_rules,
    init_intersection_data,
    make_congestion_envelope,
    make_priority_action_envelope,
    make_semaphore_state_envelope,
    make_sensor_event_envelope,
    update_intersection_from_event,
)
from pc2.db_replica import process_envelope
from pc2.traffic_light_control import apply_command, init_semaphore_states

# =============================================================================
# Helpers
# =============================================================================


def _get_config() -> CityConfig:
    """Load the real city config for testing."""
    return CityConfig()


def _make_db() -> TrafficDB:
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return TrafficDB(path)


# =============================================================================
# Traffic Rule Evaluation Tests
# =============================================================================


class TestTrafficRuleEvaluation:
    """Tests for traffic rule evaluation logic."""

    def test_normal_traffic(self):
        """Normal traffic conditions should return NORMAL with NO_ACTION."""
        config = _get_config()
        data = init_intersection_data(config)

        # Set values well within normal range: Q < 5, Vp > 35, D < 20
        data["INT-A1"]["Q"] = 2
        data["INT-A1"]["Vp"] = 45.0
        data["INT-A1"]["D"] = 5

        state, decision = evaluate_traffic_rules(data, "INT-A1", config)
        assert state == TRAFFIC_NORMAL
        assert decision == DECISION_NO_ACTION

    def test_congestion_high_queue(self):
        """High queue length should trigger congestion."""
        config = _get_config()
        data = init_intersection_data(config)

        # Q >= 10 triggers congestion
        data["INT-B2"]["Q"] = 15
        data["INT-B2"]["Vp"] = 45.0
        data["INT-B2"]["D"] = 5

        state, decision = evaluate_traffic_rules(data, "INT-B2", config)
        assert state == TRAFFIC_CONGESTION
        assert decision == DECISION_EXTEND_GREEN

    def test_congestion_low_speed(self):
        """Low average speed should trigger congestion."""
        config = _get_config()
        data = init_intersection_data(config)

        # Vp <= 20 triggers congestion
        data["INT-C3"]["Q"] = 2
        data["INT-C3"]["Vp"] = 15.0
        data["INT-C3"]["D"] = 5

        state, decision = evaluate_traffic_rules(data, "INT-C3", config)
        assert state == TRAFFIC_CONGESTION
        assert decision == DECISION_EXTEND_GREEN

    def test_congestion_high_density(self):
        """High density should trigger congestion."""
        config = _get_config()
        data = init_intersection_data(config)

        # D >= 40 triggers congestion
        data["INT-D4"]["Q"] = 2
        data["INT-D4"]["Vp"] = 45.0
        data["INT-D4"]["D"] = 45

        state, decision = evaluate_traffic_rules(data, "INT-D4", config)
        assert state == TRAFFIC_CONGESTION
        assert decision == DECISION_EXTEND_GREEN

    def test_congestion_boundary_queue(self):
        """Q exactly at congestion threshold should trigger congestion."""
        config = _get_config()
        data = init_intersection_data(config)

        data["INT-A1"]["Q"] = 10  # Exactly Q_min
        data["INT-A1"]["Vp"] = 45.0
        data["INT-A1"]["D"] = 5

        state, decision = evaluate_traffic_rules(data, "INT-A1", config)
        assert state == TRAFFIC_CONGESTION
        assert decision == DECISION_EXTEND_GREEN

    def test_congestion_boundary_speed(self):
        """Vp exactly at congestion threshold should trigger congestion."""
        config = _get_config()
        data = init_intersection_data(config)

        data["INT-A1"]["Q"] = 2
        data["INT-A1"]["Vp"] = 20.0  # Exactly Vp_max
        data["INT-A1"]["D"] = 5

        state, decision = evaluate_traffic_rules(data, "INT-A1", config)
        assert state == TRAFFIC_CONGESTION
        assert decision == DECISION_EXTEND_GREEN

    def test_default_values_normal(self):
        """Default initialized values should produce normal traffic."""
        config = _get_config()
        data = init_intersection_data(config)

        # Default: Q=0, Vp=50.0, D=0 -> all within normal range
        state, decision = evaluate_traffic_rules(data, "INT-A1", config)
        assert state == TRAFFIC_NORMAL
        assert decision == DECISION_NO_ACTION

    def test_in_between_state(self):
        """Values between normal and congestion thresholds should be NORMAL."""
        config = _get_config()
        data = init_intersection_data(config)

        # Q=7 (between 5 and 10), Vp=30 (between 20 and 35), D=15
        data["INT-A1"]["Q"] = 7
        data["INT-A1"]["Vp"] = 30.0
        data["INT-A1"]["D"] = 15

        state, decision = evaluate_traffic_rules(data, "INT-A1", config)
        # Not congested (Q < 10, Vp > 20, D < 40) but not clearly normal
        assert state == TRAFFIC_NORMAL
        assert decision == DECISION_NO_ACTION


# =============================================================================
# Intersection State Update Tests
# =============================================================================


class TestIntersectionStateUpdate:
    """Tests for updating per-intersection state from sensor events."""

    def test_camera_event_updates_queue_and_speed(self):
        """Camera event should update Q (volumen) and Vp (velocidad_promedio)."""
        config = _get_config()
        data = init_intersection_data(config)

        event = {
            "sensor_id": "CAM-A1",
            "interseccion": "INT-A1",
            "volumen": 12,
            "velocidad_promedio": 22.5,
        }

        result = update_intersection_from_event(data, TOPIC_CAMERA, event)
        assert result == "INT-A1"
        assert data["INT-A1"]["Q"] == 12
        assert data["INT-A1"]["Vp"] == 22.5

    def test_gps_event_updates_speed(self):
        """GPS event should update Vp (velocidad_promedio)."""
        config = _get_config()
        data = init_intersection_data(config)

        event = {
            "sensor_id": "GPS-A1",
            "interseccion": "INT-A1",
            "velocidad_promedio": 8.5,
            "nivel_congestion": "ALTA",
        }

        result = update_intersection_from_event(data, TOPIC_GPS, event)
        assert result == "INT-A1"
        assert data["INT-A1"]["Vp"] == 8.5
        # Q and D should remain at defaults
        assert data["INT-A1"]["Q"] == 0
        assert data["INT-A1"]["D"] == 0

    def test_inductive_event_updates_density(self):
        """Inductive event should update D (vehiculos_contados)."""
        config = _get_config()
        data = init_intersection_data(config)

        event = {
            "sensor_id": "ESP-A2",
            "interseccion": "INT-A2",
            "vehiculos_contados": 25,
            "intervalo_segundos": 30,
        }

        result = update_intersection_from_event(data, TOPIC_INDUCTIVE, event)
        assert result == "INT-A2"
        assert data["INT-A2"]["D"] == 25

    def test_unknown_intersection_returns_none(self):
        """Event for unknown intersection should return None."""
        config = _get_config()
        data = init_intersection_data(config)

        event = {
            "sensor_id": "CAM-X9",
            "interseccion": "INT-X9",
            "volumen": 5,
        }

        result = update_intersection_from_event(data, TOPIC_CAMERA, event)
        assert result is None

    def test_missing_intersection_field_returns_none(self):
        """Event without interseccion field should return None."""
        config = _get_config()
        data = init_intersection_data(config)

        event = {"sensor_id": "CAM-A1", "volumen": 5}
        result = update_intersection_from_event(data, TOPIC_CAMERA, event)
        assert result is None


# =============================================================================
# DB Envelope Tests
# =============================================================================


class TestDBEnvelopes:
    """Tests for DB write envelope creation."""

    def test_sensor_event_envelope(self):
        """Sensor event envelope should have correct type and data."""
        event = {
            "sensor_id": "CAM-A1",
            "tipo_sensor": "camara",
            "interseccion": "INT-A1",
            "volumen": 10,
            "velocidad_promedio": 25.0,
            "timestamp": "2026-01-01T10:00:00Z",
        }

        envelope_json = make_sensor_event_envelope(event, TOPIC_CAMERA)
        envelope = json.loads(envelope_json)

        assert envelope["type"] == "sensor_event"
        assert envelope["data"]["sensor_id"] == "CAM-A1"
        assert envelope["data"]["tipo_sensor"] == "camara"
        assert envelope["data"]["interseccion"] == "INT-A1"
        assert envelope["data"]["timestamp"] == "2026-01-01T10:00:00Z"

    def test_congestion_envelope(self):
        """Congestion envelope should have correct type and formatted details."""
        snapshot = {"Q": 12, "Vp": 18.5, "D": 5}
        envelope_json = make_congestion_envelope(
            "INT-B2", TRAFFIC_CONGESTION, DECISION_EXTEND_GREEN, snapshot
        )
        envelope = json.loads(envelope_json)

        assert envelope["type"] == "congestion_record"
        assert envelope["data"]["interseccion"] == "INT-B2"
        assert envelope["data"]["traffic_state"] == TRAFFIC_CONGESTION
        assert envelope["data"]["decision"] == DECISION_EXTEND_GREEN
        assert "Q=12" in envelope["data"]["details"]
        assert "Vp=18.5" in envelope["data"]["details"]

    def test_semaphore_state_envelope(self):
        """Semaphore state envelope should have correct type and fields."""
        envelope_json = make_semaphore_state_envelope(
            "INT-C3", SEMAPHORE_GREEN, SEMAPHORE_RED, "congestion", 25
        )
        envelope = json.loads(envelope_json)

        assert envelope["type"] == "semaphore_state"
        assert envelope["data"]["state_ns"] == SEMAPHORE_GREEN
        assert envelope["data"]["state_ew"] == SEMAPHORE_RED
        assert envelope["data"]["cycle_duration_sec"] == 25

    def test_priority_action_envelope(self):
        """Priority action envelope should have correct type and affected list."""
        affected = ["INT-B1", "INT-B2", "INT-B3", "INT-B4"]
        envelope_json = make_priority_action_envelope("GREEN_WAVE", "row_B", "ambulance", affected)
        envelope = json.loads(envelope_json)

        assert envelope["type"] == "priority_action"
        assert envelope["data"]["action_type"] == "GREEN_WAVE"
        assert envelope["data"]["target"] == "row_B"
        assert len(envelope["data"]["affected_intersections"]) == 4

    def test_inductive_event_uses_timestamp_fin(self):
        """Inductive events use timestamp_fin for the envelope timestamp."""
        event = {
            "sensor_id": "ESP-A2",
            "tipo_sensor": "espira_inductiva",
            "interseccion": "INT-A2",
            "vehiculos_contados": 12,
            "timestamp_inicio": "2026-01-01T10:00:00Z",
            "timestamp_fin": "2026-01-01T10:00:30Z",
        }

        envelope_json = make_sensor_event_envelope(event, TOPIC_INDUCTIVE)
        envelope = json.loads(envelope_json)
        assert envelope["data"]["timestamp"] == "2026-01-01T10:00:30Z"


# =============================================================================
# Semaphore Control Tests
# =============================================================================


class TestSemaphoreControl:
    """Tests for semaphore state management."""

    def test_init_semaphore_states(self):
        """Should initialize all 16 intersections with default state."""
        config = _get_config()
        states = init_semaphore_states(config)

        assert len(states) == 16
        for state in states.values():
            assert state.state_ns == SEMAPHORE_RED
            assert state.state_ew == SEMAPHORE_GREEN

    def test_apply_green_command_toggles(self):
        """GREEN command should toggle NS from RED to GREEN, EW from GREEN to RED."""
        config = _get_config()
        states = init_semaphore_states(config)

        cmd = SemaphoreCommand(
            interseccion="INT-B3",
            new_state=SEMAPHORE_GREEN,
            reason="congestion detected",
            duration_override_sec=25,
        )

        result = apply_command(states, cmd, config)
        assert result is not None
        assert result.state_ns == SEMAPHORE_GREEN
        assert result.state_ew == SEMAPHORE_RED
        assert result.cycle_duration_sec == 25

    def test_double_toggle(self):
        """Two consecutive GREEN commands should toggle back and forth."""
        config = _get_config()
        states = init_semaphore_states(config)

        cmd = SemaphoreCommand(
            interseccion="INT-A1",
            new_state=SEMAPHORE_GREEN,
            reason="test",
        )

        # First toggle: RED->GREEN, GREEN->RED
        apply_command(states, cmd, config)
        assert states["INT-A1"].state_ns == SEMAPHORE_GREEN
        assert states["INT-A1"].state_ew == SEMAPHORE_RED

        # Second toggle: GREEN->RED, RED->GREEN
        apply_command(states, cmd, config)
        assert states["INT-A1"].state_ns == SEMAPHORE_RED
        assert states["INT-A1"].state_ew == SEMAPHORE_GREEN

    def test_red_command_resets(self):
        """RED command should reset to default (NS=RED, EW=GREEN)."""
        config = _get_config()
        states = init_semaphore_states(config)

        # First toggle away from default
        green_cmd = SemaphoreCommand(
            interseccion="INT-A1", new_state=SEMAPHORE_GREEN, reason="test"
        )
        apply_command(states, green_cmd, config)

        # Reset with RED command
        red_cmd = SemaphoreCommand(interseccion="INT-A1", new_state=SEMAPHORE_RED, reason="reset")
        result = apply_command(states, red_cmd, config)
        assert result.state_ns == SEMAPHORE_RED
        assert result.state_ew == SEMAPHORE_GREEN

    def test_unknown_intersection_returns_none(self):
        """Command for unknown intersection should return None."""
        config = _get_config()
        states = init_semaphore_states(config)

        cmd = SemaphoreCommand(interseccion="INT-X9", new_state=SEMAPHORE_GREEN, reason="test")
        result = apply_command(states, cmd, config)
        assert result is None

    def test_default_cycle_duration(self):
        """Command without duration override should use normal cycle from config."""
        config = _get_config()
        states = init_semaphore_states(config)

        cmd = SemaphoreCommand(interseccion="INT-A1", new_state=SEMAPHORE_GREEN, reason="test")
        result = apply_command(states, cmd, config)
        assert result.cycle_duration_sec == config.normal_cycle_sec


# =============================================================================
# DB Replica Processing Tests
# =============================================================================


class TestDBReplicaProcessing:
    """Tests for DB replica envelope processing."""

    def test_process_sensor_event_envelope(self):
        """Should insert sensor event into DB from envelope."""
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
        """Should insert congestion record into DB from envelope."""
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
        """Should insert semaphore state into DB from envelope."""
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
        """Should insert priority action into DB from envelope."""
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
            # Should not raise
            process_envelope(db, envelope)
        finally:
            db.close()


# =============================================================================
# ZMQ Integration Tests
# =============================================================================


class TestAnalyticsZMQIntegration:
    """Integration tests for the analytics pipeline via ZMQ."""

    def test_sensor_event_to_semaphore_command(self):
        """
        Simulate: sensor publishes congestion event -> analytics evaluates ->
        semaphore control receives command.

        Uses direct ZMQ PUSH/PULL to verify the command flow without running
        the full analytics service process.
        """
        context = zmq.Context()

        # Semaphore PULL (simulating traffic_light_control)
        sem_pull = context.socket(zmq.PULL)
        sem_port = 16562
        sem_pull.bind(f"tcp://127.0.0.1:{sem_port}")

        # Semaphore PUSH (simulating analytics sending command)
        sem_push = context.socket(zmq.PUSH)
        sem_push.connect(f"tcp://127.0.0.1:{sem_port}")

        time.sleep(0.3)

        # Send a semaphore command (simulating what analytics would do)
        cmd = SemaphoreCommand(
            interseccion="INT-B3",
            new_state=SEMAPHORE_GREEN,
            reason="congestion detected (Q=15, Vp=12.0, D=5)",
            duration_override_sec=25,
        )
        sem_push.send_string(cmd.to_json())

        # Receive and verify
        sem_pull.setsockopt(zmq.RCVTIMEO, 2000)
        received = sem_pull.recv_string()
        data = json.loads(received)

        assert data["interseccion"] == "INT-B3"
        assert data["new_state"] == SEMAPHORE_GREEN
        assert "congestion" in data["reason"]

        sem_pull.close()
        sem_push.close()
        context.term()

    def test_db_push_pull_envelope(self):
        """
        Simulate: analytics pushes DB envelope -> replica worker receives it.

        Verifies the PUSH/PULL pattern works for DB writes.
        """
        context = zmq.Context()

        # DB PULL (simulating db_replica)
        db_pull = context.socket(zmq.PULL)
        db_port = 16564
        db_pull.bind(f"tcp://127.0.0.1:{db_port}")

        # DB PUSH (simulating analytics)
        db_push = context.socket(zmq.PUSH)
        db_push.connect(f"tcp://127.0.0.1:{db_port}")

        time.sleep(0.3)

        # Send a sensor event envelope
        event = {
            "sensor_id": "CAM-A1",
            "tipo_sensor": "camara",
            "interseccion": "INT-A1",
            "volumen": 10,
            "timestamp": "2026-01-01T10:00:00Z",
        }
        envelope_json = make_sensor_event_envelope(event, TOPIC_CAMERA)
        db_push.send_string(envelope_json)

        # Receive and verify
        db_pull.setsockopt(zmq.RCVTIMEO, 2000)
        received = db_pull.recv_string()
        envelope = json.loads(received)

        assert envelope["type"] == "sensor_event"
        assert envelope["data"]["sensor_id"] == "CAM-A1"

        db_pull.close()
        db_push.close()
        context.term()

    def test_monitoring_req_rep(self):
        """
        Simulate: monitoring sends REQ -> analytics REP responds.

        Verifies the REQ/REP pattern for monitoring queries.
        """
        context = zmq.Context()

        # REP socket (simulating analytics)
        rep = context.socket(zmq.REP)
        rep_port = 16561
        rep.bind(f"tcp://127.0.0.1:{rep_port}")

        # REQ socket (simulating monitoring on PC3)
        req = context.socket(zmq.REQ)
        req.connect(f"tcp://127.0.0.1:{rep_port}")

        time.sleep(0.3)

        # Send query
        query = MonitoringQuery(command="SYSTEM_STATUS")
        req.send_string(query.to_json())

        # Receive at REP side
        rep.setsockopt(zmq.RCVTIMEO, 2000)
        received = rep.recv_string()
        query_data = json.loads(received)
        assert query_data["command"] == "SYSTEM_STATUS"

        # Send response
        from common.models import MonitoringResponse

        response = MonitoringResponse(
            status="OK", command="SYSTEM_STATUS", message="All systems nominal"
        )
        rep.send_string(response.to_json())

        # Receive response at REQ side
        req.setsockopt(zmq.RCVTIMEO, 2000)
        resp_str = req.recv_string()
        resp_data = json.loads(resp_str)
        assert resp_data["status"] == "OK"
        assert resp_data["command"] == "SYSTEM_STATUS"

        rep.close()
        req.close()
        context.term()


# =============================================================================
# Test Area 8: Green Wave DB Persistence
# =============================================================================


class TestGreenWaveDBPersistence:
    """Tests for green wave priority action DB records."""

    def _make_db(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        return TrafficDB(path)

    def test_priority_action_persists_to_db(self):
        """A priority_action envelope should be insertable and queryable."""
        db = self._make_db()
        try:
            db.insert_priority_action(
                action_type="GREEN_WAVE",
                target="row_A",
                reason="ambulance",
                requested_by="user",
                affected_intersections=["INT-A1", "INT-A2", "INT-A3", "INT-A4"],
                timestamp="2026-01-15T08:30:00Z",
            )
            summary = db.get_system_summary()
            assert summary["total_green_waves"] >= 1
        finally:
            db.close()

    def test_semaphore_state_change_persists(self):
        """Semaphore state changes should be recorded in DB."""
        db = self._make_db()
        try:
            db.insert_semaphore_state(
                interseccion="INT-B3",
                state_ns=SEMAPHORE_GREEN,
                state_ew=SEMAPHORE_RED,
                reason="congestion",
                cycle_duration_sec=25,
                timestamp="2026-01-15T08:31:00Z",
            )
            summary = db.get_system_summary()
            assert summary["total_semaphore_changes"] >= 1
        finally:
            db.close()

    def test_congestion_record_persists_with_extend_green(self):
        """Congestion records with EXTEND_GREEN decision should be queryable."""
        db = self._make_db()
        try:
            db.insert_congestion_record(
                interseccion="INT-C2",
                traffic_state="CONGESTION",
                decision=DECISION_EXTEND_GREEN,
                details="Q=12, Vp=18, D=45",
                sensor_data={"Q": 12, "Vp": 18, "D": 45},
                timestamp="2026-01-15T09:00:00Z",
            )
            records = db.query_congestion_history(interseccion="INT-C2")
            assert len(records) == 1
            assert records[0]["decision"] == DECISION_EXTEND_GREEN
            assert records[0]["traffic_state"] == "CONGESTION"
        finally:
            db.close()
