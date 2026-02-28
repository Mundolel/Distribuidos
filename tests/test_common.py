"""
test_common.py - Unit tests for common modules (models, config, db).

Validates that the foundational components work correctly:
- Data models serialize/deserialize properly
- Configuration loads and provides expected values
- Database CRUD operations work
"""

import json
import os
import tempfile

from common.config_loader import CityConfig
from common.constants import (
    CONGESTION_ALTA,
    CONGESTION_BAJA,
    CONGESTION_NORMAL,
    SEMAPHORE_GREEN,
    SEMAPHORE_RED,
    SENSOR_TYPE_CAMERA,
    SENSOR_TYPE_INDUCTIVE,
)
from common.db_utils import TrafficDB
from common.models import (
    AnalyticsDecision,
    CameraEvent,
    GPSEvent,
    InductiveEvent,
    MonitoringQuery,
    MonitoringResponse,
    SemaphoreCommand,
    SemaphoreState,
    get_congestion_level,
)

# =============================================================================
# Model Tests
# =============================================================================


class TestCameraEvent:
    def test_creation_and_serialization(self):
        event = CameraEvent(
            sensor_id="CAM-A1",
            interseccion="INT-A1",
            volumen=10,
            velocidad_promedio=25.0,
        )
        assert event.sensor_id == "CAM-A1"
        assert event.tipo_sensor == SENSOR_TYPE_CAMERA
        assert event.volumen == 10

        data = json.loads(event.to_json())
        assert data["sensor_id"] == "CAM-A1"
        assert data["tipo_sensor"] == "camara"

    def test_from_dict(self):
        raw = {
            "sensor_id": "CAM-B2",
            "interseccion": "INT-B2",
            "volumen": 5,
            "velocidad_promedio": 40.0,
        }
        event = CameraEvent.from_dict(raw)
        assert event.sensor_id == "CAM-B2"
        assert event.volumen == 5


class TestInductiveEvent:
    def test_creation(self):
        event = InductiveEvent(
            sensor_id="ESP-C3",
            interseccion="INT-C3",
            vehiculos_contados=12,
            intervalo_segundos=30,
        )
        assert event.tipo_sensor == SENSOR_TYPE_INDUCTIVE
        assert event.vehiculos_contados == 12


class TestGPSEvent:
    def test_congestion_alta(self):
        event = GPSEvent(sensor_id="GPS-A1", interseccion="INT-A1", velocidad_promedio=8.0)
        assert event.nivel_congestion == CONGESTION_ALTA

    def test_congestion_normal(self):
        event = GPSEvent(sensor_id="GPS-A1", interseccion="INT-A1", velocidad_promedio=25.0)
        assert event.nivel_congestion == CONGESTION_NORMAL

    def test_congestion_baja(self):
        event = GPSEvent(sensor_id="GPS-A1", interseccion="INT-A1", velocidad_promedio=45.0)
        assert event.nivel_congestion == CONGESTION_BAJA


class TestCongestionLevel:
    def test_boundaries(self):
        assert get_congestion_level(9) == CONGESTION_ALTA
        assert get_congestion_level(10) == CONGESTION_NORMAL  # edge: not < 10
        assert get_congestion_level(39) == CONGESTION_NORMAL
        assert get_congestion_level(40) == CONGESTION_NORMAL  # edge: not > 40
        assert get_congestion_level(41) == CONGESTION_BAJA


class TestSemaphoreCommand:
    def test_serialization(self):
        cmd = SemaphoreCommand(
            interseccion="INT-C3", new_state=SEMAPHORE_GREEN, reason="congestion"
        )
        data = json.loads(cmd.to_json())
        assert data["new_state"] == "GREEN"
        assert data["reason"] == "congestion"


class TestMonitoringModels:
    def test_query_roundtrip(self):
        query = MonitoringQuery(command="QUERY_INTERSECTION", interseccion="INT-A1")
        data = json.loads(query.to_json())
        restored = MonitoringQuery.from_dict(data)
        assert restored.command == "QUERY_INTERSECTION"
        assert restored.interseccion == "INT-A1"

    def test_response(self):
        resp = MonitoringResponse(
            status="OK", command="QUERY_INTERSECTION", message="Found 5 events"
        )
        data = json.loads(resp.to_json())
        assert data["status"] == "OK"


class TestAnalyticsDecision:
    def test_creation(self):
        decision = AnalyticsDecision(
            interseccion="INT-B2",
            traffic_state="CONGESTION",
            decision="EXTEND_GREEN",
            details="Queue length exceeded threshold",
        )
        assert decision.traffic_state == "CONGESTION"


class TestSemaphoreState:
    def test_default_state(self):
        state = SemaphoreState(interseccion="INT-A1")
        assert state.state_ns == SEMAPHORE_RED
        assert state.state_ew == SEMAPHORE_GREEN


# =============================================================================
# Config Tests
# =============================================================================


class TestCityConfig:
    def test_loads_successfully(self):
        config = CityConfig()
        assert len(config.rows) == 4
        assert len(config.columns) == 4

    def test_intersections_count(self):
        config = CityConfig()
        assert len(config.intersections) == 16

    def test_sensors_count(self):
        config = CityConfig()
        assert len(config.cameras) == 8
        assert len(config.inductive_loops) == 8
        assert len(config.gps_sensors) == 8
        assert len(config.get_all_sensors()) == 24

    def test_semaphores_count(self):
        config = CityConfig()
        assert len(config.semaphores) == 16

    def test_zmq_address_builder(self):
        config = CityConfig()
        addr = config.zmq_address("pc2", "broker_pub")
        assert addr == "tcp://pc2:5560"

    def test_zmq_bind_address(self):
        config = CityConfig()
        addr = config.zmq_bind_address("broker_pub")
        assert addr == "tcp://*:5560"

    def test_network_hosts(self):
        config = CityConfig()
        assert config.pc1_host == "pc1"
        assert config.pc2_host == "pc2"
        assert config.pc3_host == "pc3"


# =============================================================================
# Database Tests
# =============================================================================


class TestTrafficDB:
    def _make_db(self) -> TrafficDB:
        """Create a temporary database for testing."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        return TrafficDB(path)

    def test_insert_and_query_sensor_event(self):
        db = self._make_db()
        try:
            rid = db.insert_sensor_event(
                "CAM-A1", "camara", "INT-A1", {"volumen": 10}, "2026-01-01T10:00:00Z"
            )
            assert rid == 1

            events = db.query_events_by_intersection("INT-A1")
            assert len(events) == 1
            assert events[0]["sensor_id"] == "CAM-A1"
        finally:
            db.close()

    def test_insert_and_query_congestion(self):
        db = self._make_db()
        try:
            db.insert_congestion_record(
                "INT-B2",
                "CONGESTION",
                "EXTEND_GREEN",
                "Queue > 10",
                {"Q": 12},
                "2026-01-01T10:05:00Z",
            )
            records = db.query_congestion_history(interseccion="INT-B2")
            assert len(records) == 1
            assert records[0]["traffic_state"] == "CONGESTION"
        finally:
            db.close()

    def test_insert_and_query_semaphore_state(self):
        db = self._make_db()
        try:
            db.insert_semaphore_state(
                "INT-C3", "GREEN", "RED", "congestion", 25, "2026-01-01T10:00:00Z"
            )
            state = db.query_semaphore_state("INT-C3")
            assert state is not None
            assert state["state_ns"] == "GREEN"
            assert state["state_ew"] == "RED"
        finally:
            db.close()

    def test_query_by_time_range(self):
        db = self._make_db()
        try:
            db.insert_sensor_event("CAM-A1", "camara", "INT-A1", {}, "2026-01-01T10:00:00Z")
            db.insert_sensor_event("CAM-A1", "camara", "INT-A1", {}, "2026-01-01T11:00:00Z")
            db.insert_sensor_event("CAM-A1", "camara", "INT-A1", {}, "2026-01-01T12:00:00Z")

            results = db.query_events_by_time_range("2026-01-01T10:30:00Z", "2026-01-01T11:30:00Z")
            assert len(results) == 1
        finally:
            db.close()

    def test_system_summary(self):
        db = self._make_db()
        try:
            db.insert_sensor_event("CAM-A1", "camara", "INT-A1", {}, "2026-01-01T10:00:00Z")
            db.insert_congestion_record(
                "INT-A1",
                "CONGESTION",
                "EXTEND_GREEN",
                "",
                None,
                "2026-01-01T10:00:00Z",
            )

            summary = db.get_system_summary()
            assert summary["total_sensor_events"] == 1
            assert summary["total_congestion_detections"] == 1
            assert summary["total_green_waves"] == 0
        finally:
            db.close()

    def test_event_count_in_interval(self):
        db = self._make_db()
        try:
            for i in range(5):
                db.insert_sensor_event(
                    "CAM-A1",
                    "camara",
                    "INT-A1",
                    {},
                    f"2026-01-01T10:0{i}:00Z",
                )

            count = db.get_event_count_in_interval("2026-01-01T10:00:00Z", "2026-01-01T10:04:00Z")
            assert count == 5
        finally:
            db.close()
