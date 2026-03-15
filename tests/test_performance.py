"""
test_performance.py - Unit tests for Phase 7 performance instrumentation.

Tests the latency instrumentation (SemaphoreCommand.created_at field),
the sensor count control (--sensor-count / --count args), and the
graph generation with sample data. All tests run without Docker.
"""

import json
import os
import time
from unittest.mock import patch

import pytest

from common.config_loader import get_config
from common.constants import SEMAPHORE_GREEN, SEMAPHORE_RED
from common.models import SemaphoreCommand, SemaphoreState

# =============================================================================
# Tests for SemaphoreCommand.created_at field (latency instrumentation)
# =============================================================================


class TestSemaphoreCommandCreatedAt:
    """Tests for the created_at latency measurement field."""

    def test_created_at_auto_populated(self):
        """created_at should be auto-populated with time.time() on creation."""
        before = time.time()
        cmd = SemaphoreCommand(interseccion="INT-A1", new_state="GREEN")
        after = time.time()

        assert isinstance(cmd.created_at, float)
        assert before <= cmd.created_at <= after

    def test_created_at_serialization_roundtrip(self):
        """created_at should survive to_json() -> from_dict() round-trip."""
        cmd = SemaphoreCommand(
            interseccion="INT-B2",
            new_state="GREEN",
            reason="test",
        )
        json_str = cmd.to_json()
        data = json.loads(json_str)
        restored = SemaphoreCommand.from_dict(data)

        assert restored.created_at == cmd.created_at
        assert restored.interseccion == "INT-B2"
        assert restored.new_state == "GREEN"

    def test_created_at_in_json_output(self):
        """created_at should appear as a float in the JSON output."""
        cmd = SemaphoreCommand(interseccion="INT-C3", new_state="RED")
        data = json.loads(cmd.to_json())

        assert "created_at" in data
        assert isinstance(data["created_at"], float)
        assert data["created_at"] > 0

    def test_created_at_explicit_value(self):
        """created_at should accept an explicit value."""
        cmd = SemaphoreCommand(
            interseccion="INT-D4",
            new_state="GREEN",
            created_at=1234567890.5,
        )
        assert cmd.created_at == 1234567890.5

    def test_created_at_zero_is_falsy(self):
        """created_at=0.0 should be falsy for the latency check."""
        cmd = SemaphoreCommand(
            interseccion="INT-A1",
            new_state="GREEN",
            created_at=0.0,
        )
        # The traffic_light_control checks `if command.created_at:`
        # 0.0 is falsy, so latency should NOT be logged
        assert not cmd.created_at


# =============================================================================
# Tests for apply_command latency logging
# =============================================================================


class TestApplyCommandLatency:
    """Tests for latency measurement in traffic_light_control.apply_command()."""

    def _make_states(self):
        """Create a minimal semaphore states dict for testing."""
        return {
            "INT-A1": SemaphoreState(
                interseccion="INT-A1",
                state_ns=SEMAPHORE_RED,
                state_ew=SEMAPHORE_GREEN,
            ),
        }

    def _make_config(self):
        """Create a mock config with normal_cycle_sec."""

        class MockConfig:
            normal_cycle_sec = 15

        return MockConfig()

    def test_latency_computed_when_created_at_present(self):
        """apply_command should log latency when created_at is set."""
        from pc2.traffic_light_control import apply_command

        states = self._make_states()
        config = self._make_config()

        # Create a command with a known created_at (100ms ago)
        cmd = SemaphoreCommand(
            interseccion="INT-A1",
            new_state="GREEN",
            created_at=time.time() - 0.1,
        )

        result = apply_command(states, cmd, config)
        assert result is not None
        assert result.state_ns == SEMAPHORE_GREEN  # toggled from RED

    def test_no_crash_when_created_at_zero(self):
        """apply_command should not crash when created_at is 0 (falsy)."""
        from pc2.traffic_light_control import apply_command

        states = self._make_states()
        config = self._make_config()

        cmd = SemaphoreCommand(
            interseccion="INT-A1",
            new_state="GREEN",
            created_at=0.0,
        )

        result = apply_command(states, cmd, config)
        assert result is not None


# =============================================================================
# Tests for --sensor-count argument parsing
# =============================================================================


class TestSensorCountArgs:
    """Tests for the --sensor-count CLI argument in start_pc1.py."""

    def test_sensor_count_default_zero(self):
        """Default sensor_count should be 0 (all sensors)."""
        from pc1.start_pc1 import parse_args

        with patch.dict(os.environ, {}, clear=False):
            # Remove SENSOR_COUNT from env if present
            env = os.environ.copy()
            env.pop("SENSOR_COUNT", None)
            with patch.dict(os.environ, env, clear=True):
                args = parse_args([])
                assert args.sensor_count == 0

    def test_sensor_count_cli_arg(self):
        """--sensor-count N should set sensor_count to N."""
        from pc1.start_pc1 import parse_args

        args = parse_args(["--sensor-count", "2"])
        assert args.sensor_count == 2

    def test_sensor_count_env_var(self):
        """SENSOR_COUNT env var should set sensor_count."""
        from pc1.start_pc1 import parse_args

        with patch.dict(os.environ, {"SENSOR_COUNT": "1"}):
            args = parse_args([])
            assert args.sensor_count == 1

    def test_sensor_count_cli_overrides_env(self):
        """CLI arg should override env var."""
        from pc1.start_pc1 import parse_args

        with patch.dict(os.environ, {"SENSOR_COUNT": "3"}):
            args = parse_args(["--sensor-count", "1"])
            assert args.sensor_count == 1


# =============================================================================
# Tests for sensor --count slicing
# =============================================================================


class TestSensorCountSlicing:
    """Tests for the --count arg that limits sensors loaded from config."""

    def test_camera_count_slicing(self):
        """Camera sensor --count 2 should load only first 2 cameras."""
        from pc1.sensors.camera_sensor import parse_args

        args = parse_args(["--all", "--count", "2"])
        assert args.all is True
        assert args.count == 2

        config = get_config()
        sensors = config.cameras
        if args.count > 0:
            sensors = sensors[: args.count]
        assert len(sensors) == 2
        assert sensors[0]["sensor_id"] == "CAM-A1"

    def test_inductive_count_slicing(self):
        """Inductive sensor --count 1 should load only first inductive loop."""
        from pc1.sensors.inductive_sensor import parse_args

        args = parse_args(["--all", "--count", "1"])
        assert args.all is True
        assert args.count == 1

        config = get_config()
        sensors = config.inductive_loops
        if args.count > 0:
            sensors = sensors[: args.count]
        assert len(sensors) == 1
        assert sensors[0]["sensor_id"] == "ESP-A2"

    def test_gps_count_slicing(self):
        """GPS sensor --count 2 should load only first 2 GPS sensors."""
        from pc1.sensors.gps_sensor import parse_args

        args = parse_args(["--all", "--count", "2"])
        assert args.all is True
        assert args.count == 2

        config = get_config()
        sensors = config.gps_sensors
        if args.count > 0:
            sensors = sensors[: args.count]
        assert len(sensors) == 2
        assert sensors[0]["sensor_id"] == "GPS-A1"

    def test_count_zero_loads_all(self):
        """--count 0 (default) should load all sensors."""
        from pc1.sensors.camera_sensor import parse_args

        args = parse_args(["--all"])
        assert args.count == 0

        config = get_config()
        sensors = config.cameras
        if args.count > 0:
            sensors = sensors[: args.count]
        assert len(sensors) == 8  # all cameras from config

    def test_count_exceeding_total_loads_all(self):
        """--count 100 (more than available) should load all sensors."""
        config = get_config()
        sensors = config.cameras
        count = 100
        if count > 0:
            sensors = sensors[:count]
        assert len(sensors) == 8  # can't exceed config count


# =============================================================================
# Tests for graph generation with sample data
# =============================================================================


try:
    import matplotlib  # noqa: F401

    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


@pytest.mark.skipif(not HAS_MATPLOTLIB, reason="matplotlib not installed")
class TestGraphGeneration:
    """Tests for generate_graphs.py with mock data."""

    SAMPLE_RESULTS = [
        {
            "scenario": "1A",
            "broker_mode": "standard",
            "sensor_count": 1,
            "interval_sec": 10,
            "duration_sec": 120,
            "start_time": "2026-03-14T10:00:00Z",
            "end_time": "2026-03-14T10:02:00Z",
            "throughput_events": 36,
            "latency_ms": {"min": 1.2, "max": 15.8, "avg": 5.4, "p95": 12.1, "count": 10},
        },
        {
            "scenario": "1B",
            "broker_mode": "threaded",
            "sensor_count": 1,
            "interval_sec": 10,
            "duration_sec": 120,
            "start_time": "2026-03-14T10:05:00Z",
            "end_time": "2026-03-14T10:07:00Z",
            "throughput_events": 38,
            "latency_ms": {"min": 1.0, "max": 14.2, "avg": 4.8, "p95": 11.0, "count": 12},
        },
        {
            "scenario": "2A",
            "broker_mode": "standard",
            "sensor_count": 2,
            "interval_sec": 5,
            "duration_sec": 120,
            "start_time": "2026-03-14T10:10:00Z",
            "end_time": "2026-03-14T10:12:00Z",
            "throughput_events": 144,
            "latency_ms": {"min": 2.1, "max": 25.3, "avg": 8.7, "p95": 20.0, "count": 15},
        },
        {
            "scenario": "2B",
            "broker_mode": "threaded",
            "sensor_count": 2,
            "interval_sec": 5,
            "duration_sec": 120,
            "start_time": "2026-03-14T10:15:00Z",
            "end_time": "2026-03-14T10:17:00Z",
            "throughput_events": 140,
            "latency_ms": {"min": 1.8, "max": 22.1, "avg": 7.2, "p95": 18.5, "count": 14},
        },
    ]

    def test_generate_graphs_no_crash(self, tmp_path):
        """Graph generation should not crash with valid sample data."""
        import matplotlib

        matplotlib.use("Agg")  # Non-interactive backend for testing

        from perf.generate_graphs import (
            plot_latency_by_scenario,
            plot_latency_grouped,
            plot_throughput_by_scenario,
            plot_throughput_grouped,
        )

        output_dir = tmp_path / "graphs"
        output_dir.mkdir()

        plot_throughput_grouped(self.SAMPLE_RESULTS, output_dir)
        plot_latency_grouped(self.SAMPLE_RESULTS, output_dir)
        plot_throughput_by_scenario(self.SAMPLE_RESULTS, output_dir)
        plot_latency_by_scenario(self.SAMPLE_RESULTS, output_dir)

        # Verify files were created
        assert (output_dir / "throughput_grouped.png").exists()
        assert (output_dir / "latency_grouped.png").exists()
        assert (output_dir / "throughput_by_scenario.png").exists()
        assert (output_dir / "latency_by_scenario.png").exists()

    def test_load_results_from_file(self, tmp_path):
        """load_results should correctly load a JSON results file."""
        from perf.generate_graphs import load_results

        results_file = tmp_path / "results.json"
        results_file.write_text(json.dumps(self.SAMPLE_RESULTS))

        loaded = load_results(str(results_file))
        assert len(loaded) == 4
        assert loaded[0]["scenario"] == "1A"
