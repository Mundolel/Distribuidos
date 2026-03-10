"""
test_sensors.py - Unit tests for PC1 sensor simulators and broker.

Tests sensor event generation logic (without ZMQ networking) and
validates that generated events conform to the expected data models.
"""

import json
import time

import zmq

from common.constants import (
    CAMERA_SPEED_MAX,
    CAMERA_SPEED_MIN,
    CAMERA_VOLUME_MAX,
    CAMERA_VOLUME_MIN,
    CONGESTION_ALTA,
    CONGESTION_BAJA,
    CONGESTION_NORMAL,
    GPS_SPEED_MAX,
    GPS_SPEED_MIN,
    INDUCTIVE_COUNT_MAX,
    INDUCTIVE_COUNT_MIN,
    SENSOR_TYPE_CAMERA,
    SENSOR_TYPE_GPS,
    SENSOR_TYPE_INDUCTIVE,
    TOPIC_CAMERA,
    TOPIC_GPS,
)
from common.models import GPSEvent
from pc1.sensors.camera_sensor import generate_camera_event
from pc1.sensors.gps_sensor import generate_gps_event
from pc1.sensors.inductive_sensor import generate_inductive_event

# =============================================================================
# Camera Sensor Tests
# =============================================================================


class TestCameraSensor:
    """Tests for camera sensor event generation."""

    def test_generate_event_fields(self):
        """Generated camera event should have correct sensor_id and intersection."""
        event = generate_camera_event("CAM-A1", "INT-A1")
        assert event.sensor_id == "CAM-A1"
        assert event.interseccion == "INT-A1"
        assert event.tipo_sensor == SENSOR_TYPE_CAMERA

    def test_generate_event_value_ranges(self):
        """Camera event values should be within configured ranges."""
        for _ in range(50):
            event = generate_camera_event("CAM-B2", "INT-B2")
            assert CAMERA_VOLUME_MIN <= event.volumen <= CAMERA_VOLUME_MAX
            assert CAMERA_SPEED_MIN <= event.velocidad_promedio <= CAMERA_SPEED_MAX

    def test_generate_event_has_timestamp(self):
        """Camera event should have a non-empty timestamp."""
        event = generate_camera_event("CAM-A1", "INT-A1")
        assert event.timestamp
        assert "T" in event.timestamp  # ISO 8601 format

    def test_generate_event_serialization(self):
        """Camera event should serialize to valid JSON with all fields."""
        event = generate_camera_event("CAM-C3", "INT-C3")
        json_str = event.to_json()
        data = json.loads(json_str)
        assert data["sensor_id"] == "CAM-C3"
        assert data["tipo_sensor"] == SENSOR_TYPE_CAMERA
        assert data["interseccion"] == "INT-C3"
        assert "volumen" in data
        assert "velocidad_promedio" in data
        assert "timestamp" in data

    def test_generate_event_randomness(self):
        """Multiple events should produce different values."""
        events = [generate_camera_event("CAM-A1", "INT-A1") for _ in range(20)]
        volumes = {e.volumen for e in events}
        # With 20 samples from range 0-20, we should get more than 1 distinct value
        assert len(volumes) > 1


# =============================================================================
# Inductive Sensor Tests
# =============================================================================


class TestInductiveSensor:
    """Tests for inductive loop sensor event generation."""

    def test_generate_event_fields(self):
        """Generated inductive event should have correct fields."""
        event = generate_inductive_event("ESP-A2", "INT-A2", 30)
        assert event.sensor_id == "ESP-A2"
        assert event.interseccion == "INT-A2"
        assert event.tipo_sensor == SENSOR_TYPE_INDUCTIVE
        assert event.intervalo_segundos == 30

    def test_generate_event_value_ranges(self):
        """Inductive event vehicle count should be within configured ranges."""
        for _ in range(50):
            event = generate_inductive_event("ESP-B3", "INT-B3", 30)
            assert INDUCTIVE_COUNT_MIN <= event.vehiculos_contados <= INDUCTIVE_COUNT_MAX

    def test_generate_event_timestamps(self):
        """Inductive event should have both start and end timestamps."""
        event = generate_inductive_event("ESP-A2", "INT-A2", 30)
        assert event.timestamp_inicio
        assert event.timestamp_fin
        assert "T" in event.timestamp_inicio
        assert "T" in event.timestamp_fin

    def test_generate_event_serialization(self):
        """Inductive event should serialize to valid JSON with all fields."""
        event = generate_inductive_event("ESP-C4", "INT-C4", 30)
        json_str = event.to_json()
        data = json.loads(json_str)
        assert data["sensor_id"] == "ESP-C4"
        assert data["tipo_sensor"] == SENSOR_TYPE_INDUCTIVE
        assert data["intervalo_segundos"] == 30
        assert "vehiculos_contados" in data
        assert "timestamp_inicio" in data
        assert "timestamp_fin" in data

    def test_custom_interval(self):
        """Inductive event should respect custom interval parameter."""
        event = generate_inductive_event("ESP-A2", "INT-A2", 15)
        assert event.intervalo_segundos == 15


# =============================================================================
# GPS Sensor Tests
# =============================================================================


class TestGPSSensor:
    """Tests for GPS sensor event generation."""

    def test_generate_event_fields(self):
        """Generated GPS event should have correct sensor_id and intersection."""
        event = generate_gps_event("GPS-A1", "INT-A1")
        assert event.sensor_id == "GPS-A1"
        assert event.interseccion == "INT-A1"
        assert event.tipo_sensor == SENSOR_TYPE_GPS

    def test_generate_event_value_ranges(self):
        """GPS event speed should be within configured ranges."""
        for _ in range(50):
            event = generate_gps_event("GPS-B2", "INT-B2")
            assert GPS_SPEED_MIN <= event.velocidad_promedio <= GPS_SPEED_MAX

    def test_congestion_auto_calculation(self):
        """GPS event should auto-calculate congestion level from speed."""
        # Test ALTA (speed < 10)
        event = GPSEvent(sensor_id="GPS-X", velocidad_promedio=5.0)
        assert event.nivel_congestion == CONGESTION_ALTA

        # Test NORMAL (11 <= speed <= 39)
        event = GPSEvent(sensor_id="GPS-X", velocidad_promedio=25.0)
        assert event.nivel_congestion == CONGESTION_NORMAL

        # Test BAJA (speed > 40)
        event = GPSEvent(sensor_id="GPS-X", velocidad_promedio=45.0)
        assert event.nivel_congestion == CONGESTION_BAJA

    def test_generate_event_serialization(self):
        """GPS event should serialize to valid JSON with all fields."""
        event = generate_gps_event("GPS-D2", "INT-D2")
        json_str = event.to_json()
        data = json.loads(json_str)
        assert data["sensor_id"] == "GPS-D2"
        assert data["tipo_sensor"] == SENSOR_TYPE_GPS
        assert "velocidad_promedio" in data
        assert "nivel_congestion" in data
        assert data["nivel_congestion"] in [CONGESTION_ALTA, CONGESTION_NORMAL, CONGESTION_BAJA]

    def test_generate_event_has_timestamp(self):
        """GPS event should have a non-empty timestamp."""
        event = generate_gps_event("GPS-A1", "INT-A1")
        assert event.timestamp
        assert "T" in event.timestamp


# =============================================================================
# ZMQ Integration Tests (Sensor -> Broker)
# =============================================================================


class TestSensorZMQPublish:
    """Integration tests verifying sensors can publish via ZMQ PUB/SUB."""

    def test_camera_pub_sub(self):
        """Camera sensor should publish events that a SUB socket can receive."""
        context = zmq.Context()
        port = 15555  # Use high port for testing

        # PUB socket (simulating sensor)
        pub = context.socket(zmq.PUB)
        pub.bind(f"tcp://127.0.0.1:{port}")

        # SUB socket (simulating broker)
        sub = context.socket(zmq.SUB)
        sub.connect(f"tcp://127.0.0.1:{port}")
        sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_CAMERA)

        time.sleep(0.3)  # Allow connection

        # Publish a camera event
        event = generate_camera_event("CAM-TEST", "INT-TEST")
        message = f"{TOPIC_CAMERA} {event.to_json()}"
        pub.send_string(message)

        # Receive with timeout
        sub.setsockopt(zmq.RCVTIMEO, 2000)
        received = sub.recv_string()

        assert received.startswith(TOPIC_CAMERA)
        payload = received.split(" ", 1)[1]
        data = json.loads(payload)
        assert data["sensor_id"] == "CAM-TEST"
        assert data["tipo_sensor"] == SENSOR_TYPE_CAMERA

        pub.close()
        sub.close()
        context.term()

    def test_gps_pub_sub(self):
        """GPS sensor should publish events that a SUB socket can receive."""
        context = zmq.Context()
        port = 15557

        pub = context.socket(zmq.PUB)
        pub.bind(f"tcp://127.0.0.1:{port}")

        sub = context.socket(zmq.SUB)
        sub.connect(f"tcp://127.0.0.1:{port}")
        sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_GPS)

        time.sleep(0.3)

        event = generate_gps_event("GPS-TEST", "INT-TEST")
        message = f"{TOPIC_GPS} {event.to_json()}"
        pub.send_string(message)

        sub.setsockopt(zmq.RCVTIMEO, 2000)
        received = sub.recv_string()

        assert received.startswith(TOPIC_GPS)
        payload = received.split(" ", 1)[1]
        data = json.loads(payload)
        assert data["sensor_id"] == "GPS-TEST"
        assert "nivel_congestion" in data

        pub.close()
        sub.close()
        context.term()

    def test_topic_filtering(self):
        """SUB socket should only receive messages matching its topic filter."""
        context = zmq.Context()
        port = 15558

        pub = context.socket(zmq.PUB)
        pub.bind(f"tcp://127.0.0.1:{port}")

        # Subscribe only to camera topic
        sub = context.socket(zmq.SUB)
        sub.connect(f"tcp://127.0.0.1:{port}")
        sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_CAMERA)

        time.sleep(0.3)

        # Send GPS event (should be filtered out)
        gps_event = generate_gps_event("GPS-X", "INT-X")
        pub.send_string(f"{TOPIC_GPS} {gps_event.to_json()}")

        # Send camera event (should be received)
        cam_event = generate_camera_event("CAM-X", "INT-X")
        pub.send_string(f"{TOPIC_CAMERA} {cam_event.to_json()}")

        sub.setsockopt(zmq.RCVTIMEO, 2000)
        received = sub.recv_string()

        # Should only get the camera event
        assert received.startswith(TOPIC_CAMERA)
        assert "CAM-X" in received

        pub.close()
        sub.close()
        context.term()


# =============================================================================
# Broker Forwarding Test
# =============================================================================


class TestBrokerForwarding:
    """Test that broker pattern (SUB -> PUB) correctly forwards messages."""

    def test_sub_pub_forwarding(self):
        """Messages from PUB->SUB->PUB->SUB chain should arrive intact."""
        context = zmq.Context()
        sensor_port = 15560
        broker_port = 15561

        # Sensor PUB
        sensor_pub = context.socket(zmq.PUB)
        sensor_pub.bind(f"tcp://127.0.0.1:{sensor_port}")

        # Broker SUB (from sensor)
        broker_sub = context.socket(zmq.SUB)
        broker_sub.connect(f"tcp://127.0.0.1:{sensor_port}")
        broker_sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_CAMERA)

        # Broker PUB (to analytics)
        broker_pub = context.socket(zmq.PUB)
        broker_pub.bind(f"tcp://127.0.0.1:{broker_port}")

        # Analytics SUB (from broker)
        analytics_sub = context.socket(zmq.SUB)
        analytics_sub.connect(f"tcp://127.0.0.1:{broker_port}")
        analytics_sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_CAMERA)

        time.sleep(0.5)  # Allow all connections

        # Sensor publishes
        event = generate_camera_event("CAM-FWD", "INT-FWD")
        original_msg = f"{TOPIC_CAMERA} {event.to_json()}"
        sensor_pub.send_string(original_msg)

        # Broker receives and forwards
        broker_sub.setsockopt(zmq.RCVTIMEO, 2000)
        received_at_broker = broker_sub.recv_string()
        broker_pub.send_string(received_at_broker)

        # Analytics receives
        analytics_sub.setsockopt(zmq.RCVTIMEO, 2000)
        received_at_analytics = analytics_sub.recv_string()

        # Verify message integrity
        assert received_at_analytics == original_msg
        payload = received_at_analytics.split(" ", 1)[1]
        data = json.loads(payload)
        assert data["sensor_id"] == "CAM-FWD"

        # Cleanup
        sensor_pub.close()
        broker_sub.close()
        broker_pub.close()
        analytics_sub.close()
        context.term()
