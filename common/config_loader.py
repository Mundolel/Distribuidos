"""
config_loader.py - Loads and provides access to the city configuration.

Reads config/city_config.json and provides helper methods to access
grid layout, sensor mappings, ZMQ ports, traffic rules, and timings.
"""

import json
import os
from typing import Any

# Default config path (relative to project root)
DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "city_config.json"
)


class CityConfig:
    """
    Loads and provides structured access to the city configuration file.
    """

    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH):
        """Load configuration from JSON file."""
        with open(config_path, encoding="utf-8") as f:
            self._config = json.load(f)

    # =========================================================================
    # Grid
    # =========================================================================

    @property
    def rows(self) -> list[str]:
        """Grid row labels (e.g., ['A', 'B', 'C', 'D'])."""
        return self._config["city"]["grid"]["rows"]

    @property
    def columns(self) -> list[int]:
        """Grid column numbers (e.g., [1, 2, 3, 4])."""
        return self._config["city"]["grid"]["columns"]

    @property
    def intersections(self) -> list[str]:
        """All intersection IDs (e.g., ['INT-A1', ..., 'INT-D4'])."""
        return self._config["intersections"]

    # =========================================================================
    # Sensors
    # =========================================================================

    @property
    def cameras(self) -> list[dict[str, str]]:
        """Camera sensor definitions."""
        return self._config["sensors"]["cameras"]

    @property
    def inductive_loops(self) -> list[dict[str, str]]:
        """Inductive loop sensor definitions."""
        return self._config["sensors"]["inductive_loops"]

    @property
    def gps_sensors(self) -> list[dict[str, str]]:
        """GPS sensor definitions."""
        return self._config["sensors"]["gps"]

    def get_all_sensors(self) -> list[dict[str, str]]:
        """Return all sensors across all types."""
        return self.cameras + self.inductive_loops + self.gps_sensors

    def get_sensors_at_intersection(self, intersection: str) -> list[dict[str, str]]:
        """Get all sensors located at a given intersection."""
        return [s for s in self.get_all_sensors() if s["interseccion"] == intersection]

    # =========================================================================
    # Semaphores
    # =========================================================================

    @property
    def semaphores(self) -> list[str]:
        """All semaphore IDs."""
        return self._config["semaphores"]["list"]

    @property
    def semaphore_initial_state(self) -> str:
        """Initial state for all semaphores."""
        return self._config["semaphores"]["initial_state"]

    # =========================================================================
    # Rules
    # =========================================================================

    @property
    def rules(self) -> dict[str, Any]:
        """Full rules configuration."""
        return self._config["rules"]

    @property
    def normal_rule(self) -> dict[str, Any]:
        """Normal traffic conditions."""
        return self._config["rules"]["normal"]["conditions"]

    @property
    def congestion_rule(self) -> dict[str, Any]:
        """Congestion detection conditions."""
        return self._config["rules"]["congestion"]["conditions"]

    # =========================================================================
    # Timings
    # =========================================================================

    @property
    def timings(self) -> dict[str, int]:
        """All timing parameters."""
        return self._config["timings"]

    @property
    def normal_cycle_sec(self) -> int:
        return self._config["timings"]["normal_cycle_sec"]

    @property
    def congestion_extension_sec(self) -> int:
        return self._config["timings"]["congestion_extension_sec"]

    @property
    def green_wave_duration_sec(self) -> int:
        return self._config["timings"]["green_wave_duration_sec"]

    @property
    def sensor_default_interval_sec(self) -> int:
        return self._config["timings"]["sensor_default_interval_sec"]

    @property
    def inductive_interval_sec(self) -> int:
        return self._config["timings"]["inductive_interval_sec"]

    @property
    def health_check_interval_sec(self) -> int:
        return self._config["timings"]["health_check_interval_sec"]

    @property
    def health_check_timeout_ms(self) -> int:
        return self._config["timings"]["health_check_timeout_ms"]

    @property
    def health_check_max_retries(self) -> int:
        return self._config["timings"]["health_check_max_retries"]

    # =========================================================================
    # ZMQ Ports
    # =========================================================================

    @property
    def zmq_ports(self) -> dict[str, int]:
        """All ZMQ port assignments."""
        return self._config["zmq_ports"]

    def get_port(self, name: str) -> int:
        """Get a specific ZMQ port by name."""
        return self._config["zmq_ports"][name]

    # =========================================================================
    # ZMQ Topics
    # =========================================================================

    @property
    def zmq_topics(self) -> dict[str, str]:
        """ZMQ topic strings for PUB/SUB."""
        return self._config["zmq_topics"]

    # =========================================================================
    # Network
    # =========================================================================

    @property
    def pc1_host(self) -> str:
        return self._config["network"]["pc1_host"]

    @property
    def pc2_host(self) -> str:
        return self._config["network"]["pc2_host"]

    @property
    def pc3_host(self) -> str:
        return self._config["network"]["pc3_host"]

    # =========================================================================
    # Convenience: Build ZMQ addresses
    # =========================================================================

    def zmq_address(self, host: str, port_name: str) -> str:
        """Build a tcp:// ZMQ address string."""
        port = self.get_port(port_name)
        return f"tcp://{host}:{port}"

    def zmq_bind_address(self, port_name: str) -> str:
        """Build a tcp://*: ZMQ bind address string."""
        port = self.get_port(port_name)
        return f"tcp://*:{port}"


# Singleton-like convenience: load once and reuse
_config_instance: CityConfig | None = None


def get_config(config_path: str = DEFAULT_CONFIG_PATH) -> CityConfig:
    """Get or create the global CityConfig instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = CityConfig(config_path)
    return _config_instance
