"""
constants.py - Shared constants for the distributed traffic management system.

Defines ZMQ topics, congestion thresholds, sensor types, semaphore states,
and system-wide enumerations used across all components.
"""

# =============================================================================
# ZMQ Topics - Used for PUB/SUB filtering
# =============================================================================
TOPIC_CAMERA = "camara"
TOPIC_INDUCTIVE = "espira"
TOPIC_GPS = "gps"

ALL_SENSOR_TOPICS = [TOPIC_CAMERA, TOPIC_INDUCTIVE, TOPIC_GPS]

# =============================================================================
# Sensor Types
# =============================================================================
SENSOR_TYPE_CAMERA = "camara"
SENSOR_TYPE_INDUCTIVE = "espira_inductiva"
SENSOR_TYPE_GPS = "gps"

# =============================================================================
# Congestion Level Thresholds (based on average speed - km/h)
# =============================================================================
CONGESTION_ALTA_MAX_SPEED = 10  # velocidad_promedio < 10 -> ALTA
CONGESTION_NORMAL_MIN_SPEED = 11  # 11 <= velocidad_promedio <= 39 -> NORMAL
CONGESTION_NORMAL_MAX_SPEED = 39
CONGESTION_BAJA_MIN_SPEED = 40  # velocidad_promedio > 40 -> BAJA

# =============================================================================
# Congestion Level Labels
# =============================================================================
CONGESTION_ALTA = "ALTA"
CONGESTION_NORMAL = "NORMAL"
CONGESTION_BAJA = "BAJA"

# =============================================================================
# Traffic State Labels
# =============================================================================
TRAFFIC_NORMAL = "NORMAL"
TRAFFIC_CONGESTION = "CONGESTION"
TRAFFIC_GREEN_WAVE = "GREEN_WAVE"

# =============================================================================
# Traffic Rule Thresholds (default values, also defined in config)
# =============================================================================
# Normal traffic: Q < Q_MAX and Vp > VP_MIN and D < D_MAX
RULE_NORMAL_Q_MAX = 5  # Max queue length for normal traffic
RULE_NORMAL_VP_MIN = 35  # Min average speed for normal traffic (km/h)
RULE_NORMAL_D_MAX = 20  # Max density for normal traffic (veh/km)

# Congestion: Q >= Q_MIN or Vp <= VP_MAX or D >= D_MIN
RULE_CONGESTION_Q_MIN = 10  # Min queue length to trigger congestion
RULE_CONGESTION_VP_MAX = 20  # Max average speed to trigger congestion (km/h)
RULE_CONGESTION_D_MIN = 40  # Min density to trigger congestion (veh/km)

# =============================================================================
# Semaphore States
# =============================================================================
SEMAPHORE_GREEN = "GREEN"
SEMAPHORE_RED = "RED"

# =============================================================================
# Semaphore Timing (seconds)
# =============================================================================
NORMAL_CYCLE_SEC = 15  # Normal red-to-green wait time
CONGESTION_EXTENSION_SEC = 10  # Extra green time during congestion
GREEN_WAVE_DURATION_SEC = 30  # How long green wave lasts

# =============================================================================
# Sensor Generation Defaults
# =============================================================================
SENSOR_DEFAULT_INTERVAL_SEC = 10
INDUCTIVE_INTERVAL_SEC = 30

# Sensor value ranges for random generation
CAMERA_VOLUME_MIN = 0
CAMERA_VOLUME_MAX = 20
CAMERA_SPEED_MIN = 5
CAMERA_SPEED_MAX = 50

INDUCTIVE_COUNT_MIN = 0
INDUCTIVE_COUNT_MAX = 30

GPS_SPEED_MIN = 5
GPS_SPEED_MAX = 55

# =============================================================================
# Health Check Parameters
# =============================================================================
HEALTH_CHECK_INTERVAL_SEC = 5
HEALTH_CHECK_TIMEOUT_MS = 2000
HEALTH_CHECK_MAX_RETRIES = 3
HEALTH_CHECK_MSG = "PING"
HEALTH_CHECK_RESPONSE = "PONG"

# =============================================================================
# Monitoring Command Types
# =============================================================================
CMD_QUERY_INTERSECTION = "QUERY_INTERSECTION"
CMD_QUERY_HISTORY = "QUERY_HISTORY"
CMD_FORCE_GREEN_WAVE = "FORCE_GREEN_WAVE"
CMD_FORCE_SEMAPHORE = "FORCE_SEMAPHORE"
CMD_SYSTEM_STATUS = "SYSTEM_STATUS"
CMD_HEALTH_CHECK = "HEALTH_CHECK"

# =============================================================================
# Analytics Decision Types (for logging/DB)
# =============================================================================
DECISION_NO_ACTION = "NO_ACTION"
DECISION_EXTEND_GREEN = "EXTEND_GREEN"
DECISION_GREEN_WAVE = "GREEN_WAVE"
DECISION_FORCE_CHANGE = "FORCE_CHANGE"

# =============================================================================
# System Status
# =============================================================================
STATUS_OK = "OK"
STATUS_FAILOVER = "FAILOVER"
STATUS_ERROR = "ERROR"
