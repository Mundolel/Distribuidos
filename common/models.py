"""
models.py - Data models for the distributed traffic management system.

Defines dataclasses for sensor events, semaphore commands, monitoring queries,
and responses. All models support JSON serialization/deserialization for
transmission over ZMQ sockets.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from common.constants import (
    CONGESTION_ALTA,
    CONGESTION_ALTA_MAX_SPEED,
    CONGESTION_BAJA,
    CONGESTION_BAJA_MIN_SPEED,
    CONGESTION_NORMAL,
    SENSOR_TYPE_CAMERA,
    SENSOR_TYPE_GPS,
    SENSOR_TYPE_INDUCTIVE,
)

# =============================================================================
# Utility Functions
# =============================================================================


def now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_json(obj) -> str:
    """Serialize a dataclass instance to a JSON string."""
    return json.dumps(asdict(obj), ensure_ascii=False)


def from_json(json_str: str) -> dict:
    """Deserialize a JSON string to a dictionary."""
    return json.loads(json_str)


def get_congestion_level(velocidad_promedio: float) -> str:
    """
    Determine congestion level based on average speed.
    - ALTA: speed < 10 km/h
    - NORMAL: 11 <= speed <= 39 km/h
    - BAJA: speed > 40 km/h
    """
    if velocidad_promedio < CONGESTION_ALTA_MAX_SPEED:
        return CONGESTION_ALTA
    elif velocidad_promedio > CONGESTION_BAJA_MIN_SPEED:
        return CONGESTION_BAJA
    else:
        return CONGESTION_NORMAL


# =============================================================================
# Sensor Event Models
# =============================================================================


@dataclass
class CameraEvent:
    """
    Event from a camera sensor (EVENTO_LONGITUD_COLA - Lq).
    Measures queue length (vehicles waiting) and average speed at an intersection.
    """

    sensor_id: str
    tipo_sensor: str = field(default=SENSOR_TYPE_CAMERA)
    interseccion: str = ""
    volumen: int = 0  # Number of vehicles waiting at semaphore
    velocidad_promedio: float = 0.0  # Average speed in km/h (max 50)
    timestamp: str = field(default_factory=now_iso)

    def to_json(self) -> str:
        return to_json(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CameraEvent":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class InductiveEvent:
    """
    Event from an inductive loop sensor (EVENTO_CONTEO_VEHICULAR - Cv).
    Counts vehicles that have passed over the loop in a time interval.
    """

    sensor_id: str
    tipo_sensor: str = field(default=SENSOR_TYPE_INDUCTIVE)
    interseccion: str = ""
    vehiculos_contados: int = 0  # Vehicles counted over the loop
    intervalo_segundos: int = 30  # Measurement interval (matches semaphore cycle)
    timestamp_inicio: str = field(default_factory=now_iso)
    timestamp_fin: str = ""

    def to_json(self) -> str:
        return to_json(self)

    @classmethod
    def from_dict(cls, data: dict) -> "InductiveEvent":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class GPSEvent:
    """
    Event from a GPS sensor (EVENTO_DENSIDAD_DE_TRAFICO - Dt).
    Reports traffic density and congestion level based on average speed.
    """

    sensor_id: str
    tipo_sensor: str = field(default=SENSOR_TYPE_GPS)
    interseccion: str = ""
    nivel_congestion: str = CONGESTION_NORMAL
    velocidad_promedio: float = 0.0  # Average speed in km/h
    timestamp: str = field(default_factory=now_iso)

    def __post_init__(self):
        """Auto-calculate congestion level from average speed."""
        self.nivel_congestion = get_congestion_level(self.velocidad_promedio)

    def to_json(self) -> str:
        return to_json(self)

    @classmethod
    def from_dict(cls, data: dict) -> "GPSEvent":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# =============================================================================
# Semaphore Command Model
# =============================================================================


@dataclass
class SemaphoreCommand:
    """
    Command sent from the analytics service to the semaphore control service.
    Instructs a semaphore to change its state.
    """

    interseccion: str
    new_state: str  # "GREEN" or "RED"
    reason: str = ""  # Why the change is being made
    duration_override_sec: int | None = None  # Custom duration (None = default)
    timestamp: str = field(default_factory=now_iso)

    def to_json(self) -> str:
        return to_json(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SemaphoreCommand":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# =============================================================================
# Monitoring Query / Response Models
# =============================================================================


@dataclass
class MonitoringQuery:
    """
    Query sent from the monitoring service (PC3) to the analytics service (PC2).
    """

    command: str  # Command type (see constants CMD_*)
    interseccion: str | None = None
    timestamp_inicio: str | None = None
    timestamp_fin: str | None = None
    row: str | None = None  # For green wave: row letter (e.g., "B")
    column: int | None = None  # For green wave: column number (e.g., 3)
    new_state: str | None = None  # For force semaphore change
    reason: str | None = None
    timestamp: str = field(default_factory=now_iso)

    def to_json(self) -> str:
        return to_json(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MonitoringQuery":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class MonitoringResponse:
    """
    Response sent from the analytics service (PC2) back to monitoring (PC3).
    """

    status: str = "OK"  # OK, ERROR, etc.
    command: str = ""  # Original command type
    message: str = ""  # Human-readable message
    data: dict | None = None  # Query result data
    timestamp: str = field(default_factory=now_iso)

    def to_json(self) -> str:
        # Custom serialization because 'data' can be complex
        d = asdict(self)
        return json.dumps(d, ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, data: dict) -> "MonitoringResponse":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# =============================================================================
# Analytics Decision Record (for DB logging)
# =============================================================================


@dataclass
class AnalyticsDecision:
    """
    Record of a decision made by the analytics service.
    Stored in the database for historical queries.
    """

    interseccion: str
    traffic_state: str  # NORMAL, CONGESTION, GREEN_WAVE
    decision: str  # NO_ACTION, EXTEND_GREEN, GREEN_WAVE, FORCE_CHANGE
    details: str = ""  # Human-readable details
    sensor_data: dict | None = None  # Snapshot of sensor readings
    timestamp: str = field(default_factory=now_iso)

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, data: dict) -> "AnalyticsDecision":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# =============================================================================
# Semaphore State Record (for DB / in-memory tracking)
# =============================================================================


@dataclass
class SemaphoreState:
    """
    Current state of a semaphore at a given intersection.
    """

    interseccion: str
    state_ns: str = "RED"  # North-South direction state
    state_ew: str = "GREEN"  # East-West direction state
    last_change: str = field(default_factory=now_iso)
    cycle_duration_sec: int = 15  # Current cycle duration

    def to_json(self) -> str:
        return to_json(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SemaphoreState":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
