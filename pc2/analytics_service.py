"""
analytics_service.py - Core analytics engine for the traffic management system (PC2).

This is the central processing component that:
  1. Receives sensor events from the broker (PC1) via PUB/SUB
  2. Evaluates traffic rules per intersection to detect congestion
  3. Sends semaphore control commands when state changes are needed
  4. Pushes all data to both primary (PC3) and replica (PC2) databases
  5. Responds to monitoring queries from the monitoring service (PC3) via REQ/REP

ZMQ Sockets:
    - SUB:  connects to broker PUB on PC1 (tcp://pc1:5560)
    - REP:  binds for monitoring queries (tcp://*:5561)
    - PUSH: connects to semaphore control PULL (tcp://127.0.0.1:5562)
    - PUSH: connects to primary DB PULL on PC3 (tcp://pc3:5563)
    - PUSH: connects to replica DB PULL on PC2 (tcp://127.0.0.1:5564)

Traffic Rules:
    - Normal:     Q < 5  AND Vp > 35 AND D < 20  -> standard 15s cycle
    - Congestion: Q >= 10 OR Vp <= 20 OR D >= 40  -> extend green +10s
    - Green Wave: user command only                -> force GREEN on route for 30s

Usage:
    python -m pc2.analytics_service
"""

import json
import logging
import signal

import zmq

from common.config_loader import get_config
from common.constants import (
    ALL_SENSOR_TOPICS,
    CMD_FORCE_GREEN_WAVE,
    CMD_FORCE_SEMAPHORE,
    CMD_HEALTH_CHECK,
    CMD_QUERY_HISTORY,
    CMD_QUERY_INTERSECTION,
    CMD_SYSTEM_STATUS,
    DECISION_EXTEND_GREEN,
    DECISION_GREEN_WAVE,
    DECISION_NO_ACTION,
    HEALTH_CHECK_RESPONSE,
    SEMAPHORE_GREEN,
    SEMAPHORE_RED,
    SENSOR_TYPE_CAMERA,
    SENSOR_TYPE_GPS,
    SENSOR_TYPE_INDUCTIVE,
    STATUS_OK,
    TOPIC_CAMERA,
    TOPIC_GPS,
    TOPIC_INDUCTIVE,
    TRAFFIC_CONGESTION,
    TRAFFIC_GREEN_WAVE,
    TRAFFIC_NORMAL,
)
from common.db_utils import TrafficDB
from common.models import (
    MonitoringQuery,
    MonitoringResponse,
    SemaphoreCommand,
    from_json,
    now_iso,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("Analytics")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_running = True


def _signal_handler(sig, frame):
    global _running
    logger.info("Shutdown signal received, stopping analytics service...")
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ---------------------------------------------------------------------------
# Per-intersection state tracking
# ---------------------------------------------------------------------------


def init_intersection_data(config) -> dict[str, dict]:
    """
    Initialize per-intersection data tracking with default (normal) values.

    Returns:
        Dict mapping intersection IDs to their latest sensor readings and state.
    """
    data = {}
    for intersection in config.intersections:
        data[intersection] = {
            "Q": 0,  # Queue length (from camera: volumen)
            "Vp": 50.0,  # Average speed (from camera/GPS: velocidad_promedio)
            "D": 0,  # Density proxy (from inductive: vehiculos_contados)
            "traffic_state": TRAFFIC_NORMAL,
            "last_updated": now_iso(),
        }
    return data


def update_intersection_from_event(
    intersection_data: dict[str, dict],
    topic: str,
    event_data: dict,
) -> str | None:
    """
    Update the per-intersection tracking data from a received sensor event.

    Args:
        intersection_data: The tracking dictionary.
        topic: ZMQ topic string ("camara", "espira", "gps").
        event_data: Parsed event JSON data.

    Returns:
        The intersection ID that was updated, or None if not found.
    """
    intersection = event_data.get("interseccion")
    if not intersection or intersection not in intersection_data:
        return None

    entry = intersection_data[intersection]
    entry["last_updated"] = now_iso()

    if topic == TOPIC_CAMERA:
        entry["Q"] = event_data.get("volumen", entry["Q"])
        entry["Vp"] = event_data.get("velocidad_promedio", entry["Vp"])
    elif topic == TOPIC_GPS:
        entry["Vp"] = event_data.get("velocidad_promedio", entry["Vp"])
    elif topic == TOPIC_INDUCTIVE:
        entry["D"] = event_data.get("vehiculos_contados", entry["D"])

    return intersection


# ---------------------------------------------------------------------------
# Traffic rule evaluation
# ---------------------------------------------------------------------------


def evaluate_traffic_rules(
    intersection_data: dict[str, dict],
    intersection: str,
    config,
) -> tuple[str, str]:
    """
    Evaluate traffic rules for a given intersection based on current sensor data.

    Rules:
        Normal:     Q < Q_max AND Vp > Vp_min AND D < D_max
        Congestion: Q >= Q_min OR Vp <= Vp_max OR D >= D_min

    Args:
        intersection_data: Current per-intersection readings.
        intersection: The intersection ID to evaluate.
        config: CityConfig instance.

    Returns:
        Tuple of (traffic_state, decision) where:
        - traffic_state: NORMAL, CONGESTION, or GREEN_WAVE
        - decision: NO_ACTION, EXTEND_GREEN, etc.
    """
    entry = intersection_data[intersection]
    queue = entry["Q"]
    speed = entry["Vp"]
    density = entry["D"]

    # Get thresholds from config
    normal_rules = config.normal_rule
    congestion_rules = config.congestion_rule

    q_max = normal_rules["Q_max"]
    vp_min = normal_rules["Vp_min"]
    d_max = normal_rules["D_max"]

    q_min = congestion_rules["Q_min"]
    vp_max = congestion_rules["Vp_max"]
    d_min = congestion_rules["D_min"]

    # Check for congestion first (higher priority)
    if queue >= q_min or speed <= vp_max or density >= d_min:
        entry["traffic_state"] = TRAFFIC_CONGESTION
        return TRAFFIC_CONGESTION, DECISION_EXTEND_GREEN

    # Check for normal traffic
    if queue < q_max and speed > vp_min and density < d_max:
        entry["traffic_state"] = TRAFFIC_NORMAL
        return TRAFFIC_NORMAL, DECISION_NO_ACTION

    # In-between state (not clearly normal nor congested) - no action
    entry["traffic_state"] = TRAFFIC_NORMAL
    return TRAFFIC_NORMAL, DECISION_NO_ACTION


# ---------------------------------------------------------------------------
# DB envelope helpers
# ---------------------------------------------------------------------------


def make_sensor_event_envelope(event_data: dict, topic: str) -> str:
    """Create a DB write envelope for a sensor event."""
    # Determine tipo_sensor from topic
    tipo_sensor_map = {
        TOPIC_CAMERA: SENSOR_TYPE_CAMERA,
        TOPIC_INDUCTIVE: SENSOR_TYPE_INDUCTIVE,
        TOPIC_GPS: SENSOR_TYPE_GPS,
    }
    tipo_sensor = tipo_sensor_map.get(topic, event_data.get("tipo_sensor", "unknown"))

    # Use the appropriate timestamp field
    timestamp = event_data.get("timestamp") or event_data.get("timestamp_fin", now_iso())

    envelope = {
        "type": "sensor_event",
        "data": {
            "sensor_id": event_data.get("sensor_id", "unknown"),
            "tipo_sensor": tipo_sensor,
            "interseccion": event_data.get("interseccion", "unknown"),
            "event_data": event_data,
            "timestamp": timestamp,
        },
    }
    return json.dumps(envelope, ensure_ascii=False)


def make_congestion_envelope(
    intersection: str,
    traffic_state: str,
    decision: str,
    sensor_snapshot: dict,
) -> str:
    """Create a DB write envelope for a congestion record."""
    details_parts = []
    if "Q" in sensor_snapshot:
        details_parts.append(f"Q={sensor_snapshot['Q']}")
    if "Vp" in sensor_snapshot:
        details_parts.append(f"Vp={sensor_snapshot['Vp']}")
    if "D" in sensor_snapshot:
        details_parts.append(f"D={sensor_snapshot['D']}")

    envelope = {
        "type": "congestion_record",
        "data": {
            "interseccion": intersection,
            "traffic_state": traffic_state,
            "decision": decision,
            "details": ", ".join(details_parts),
            "sensor_data": sensor_snapshot,
            "timestamp": now_iso(),
        },
    }
    return json.dumps(envelope, ensure_ascii=False)


def make_semaphore_state_envelope(
    intersection: str,
    state_ns: str,
    state_ew: str,
    reason: str,
    cycle_duration_sec: int,
) -> str:
    """Create a DB write envelope for a semaphore state change."""
    envelope = {
        "type": "semaphore_state",
        "data": {
            "interseccion": intersection,
            "state_ns": state_ns,
            "state_ew": state_ew,
            "reason": reason,
            "cycle_duration_sec": cycle_duration_sec,
            "timestamp": now_iso(),
        },
    }
    return json.dumps(envelope, ensure_ascii=False)


def make_priority_action_envelope(
    action_type: str,
    target: str,
    reason: str,
    affected_intersections: list[str],
) -> str:
    """Create a DB write envelope for a priority action."""
    envelope = {
        "type": "priority_action",
        "data": {
            "action_type": action_type,
            "target": target,
            "reason": reason,
            "requested_by": "user",
            "affected_intersections": affected_intersections,
            "timestamp": now_iso(),
        },
    }
    return json.dumps(envelope, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Monitoring query handlers
# ---------------------------------------------------------------------------


def handle_monitoring_query(
    query: MonitoringQuery,
    intersection_data: dict[str, dict],
    local_db: TrafficDB,
    config,
    semaphore_push: zmq.Socket,
    db_primary_push: zmq.Socket,
    db_replica_push: zmq.Socket,
) -> MonitoringResponse:
    """
    Handle a monitoring query from PC3 and return a response.

    Args:
        query: The parsed MonitoringQuery.
        intersection_data: Current per-intersection state.
        local_db: Local replica DB for queries (used during failover too).
        config: CityConfig instance.
        semaphore_push: PUSH socket to semaphore control.
        db_primary_push: PUSH socket to primary DB.
        db_replica_push: PUSH socket to replica DB.

    Returns:
        MonitoringResponse with the result.
    """
    cmd = query.command

    if cmd == CMD_QUERY_INTERSECTION:
        return _handle_query_intersection(query, intersection_data, local_db)
    elif cmd == CMD_QUERY_HISTORY:
        return _handle_query_history(query, local_db)
    elif cmd == CMD_FORCE_GREEN_WAVE:
        return _handle_force_green_wave(
            query, intersection_data, config, semaphore_push, db_primary_push, db_replica_push
        )
    elif cmd == CMD_FORCE_SEMAPHORE:
        return _handle_force_semaphore(
            query, config, semaphore_push, db_primary_push, db_replica_push
        )
    elif cmd == CMD_SYSTEM_STATUS:
        return _handle_system_status(intersection_data, local_db)
    elif cmd == CMD_HEALTH_CHECK:
        return MonitoringResponse(
            status=STATUS_OK,
            command=CMD_HEALTH_CHECK,
            message=HEALTH_CHECK_RESPONSE,
        )
    else:
        return MonitoringResponse(
            status="ERROR",
            command=cmd,
            message=f"Unknown command: {cmd}",
        )


def _handle_query_intersection(
    query: MonitoringQuery,
    intersection_data: dict[str, dict],
    local_db: TrafficDB,
) -> MonitoringResponse:
    """Handle QUERY_INTERSECTION: return current state + recent events."""
    intersection = query.interseccion
    if not intersection or intersection not in intersection_data:
        return MonitoringResponse(
            status="ERROR",
            command=CMD_QUERY_INTERSECTION,
            message=f"Intersection not found: {intersection}",
        )

    current = intersection_data[intersection].copy()
    recent_events = local_db.query_events_by_intersection(intersection, limit=10)
    semaphore = local_db.query_semaphore_state(intersection)

    return MonitoringResponse(
        status=STATUS_OK,
        command=CMD_QUERY_INTERSECTION,
        message=f"Current state of {intersection}",
        data={
            "intersection": intersection,
            "current_readings": current,
            "semaphore_state": semaphore,
            "recent_events_count": len(recent_events),
            "recent_events": recent_events[:5],
        },
    )


def _handle_query_history(
    query: MonitoringQuery,
    local_db: TrafficDB,
) -> MonitoringResponse:
    """Handle QUERY_HISTORY: return congestion history in time range."""
    records = local_db.query_congestion_history(
        timestamp_inicio=query.timestamp_inicio,
        timestamp_fin=query.timestamp_fin,
        interseccion=query.interseccion,
    )
    return MonitoringResponse(
        status=STATUS_OK,
        command=CMD_QUERY_HISTORY,
        message=f"Found {len(records)} congestion records",
        data={"records": records},
    )


def _handle_force_green_wave(
    query: MonitoringQuery,
    intersection_data: dict[str, dict],
    config,
    semaphore_push: zmq.Socket,
    db_primary_push: zmq.Socket,
    db_replica_push: zmq.Socket,
) -> MonitoringResponse:
    """Handle FORCE_GREEN_WAVE: force green on all intersections in a row or column."""
    row = query.row
    column = query.column
    reason = query.reason or "green wave (user command)"

    if not row and not column:
        return MonitoringResponse(
            status="ERROR",
            command=CMD_FORCE_GREEN_WAVE,
            message="Must specify 'row' or 'column' for green wave",
        )

    # Find affected intersections
    affected = []
    for intersection in config.intersections:
        # Intersection format: INT-XN where X is row letter, N is column number
        parts = intersection.split("-")[1]  # e.g., "A1"
        int_row = parts[0]  # e.g., "A"
        int_col = int(parts[1:])  # e.g., 1

        if (row and int_row == row) or (column and int_col == column):
            affected.append(intersection)

    if not affected:
        return MonitoringResponse(
            status="ERROR",
            command=CMD_FORCE_GREEN_WAVE,
            message=f"No intersections found for row={row}, column={column}",
        )

    # Send GREEN command to all affected intersections
    green_wave_duration = config.green_wave_duration_sec
    for intersection in affected:
        cmd = SemaphoreCommand(
            interseccion=intersection,
            new_state=SEMAPHORE_GREEN,
            reason=reason,
            duration_override_sec=green_wave_duration,
        )
        semaphore_push.send_string(cmd.to_json())

        # Update in-memory state
        if intersection in intersection_data:
            intersection_data[intersection]["traffic_state"] = TRAFFIC_GREEN_WAVE

        # Push semaphore state to DBs
        state_envelope = make_semaphore_state_envelope(
            intersection, SEMAPHORE_GREEN, SEMAPHORE_RED, reason, green_wave_duration
        )
        db_primary_push.send_string(state_envelope)
        db_replica_push.send_string(state_envelope)

    # Record priority action
    target = f"row_{row}" if row else f"col_{column}"
    action_envelope = make_priority_action_envelope(DECISION_GREEN_WAVE, target, reason, affected)
    db_primary_push.send_string(action_envelope)
    db_replica_push.send_string(action_envelope)

    logger.info(
        "[GREEN WAVE] %s -> %d intersections: %s (reason: %s)",
        target,
        len(affected),
        affected,
        reason,
    )

    return MonitoringResponse(
        status=STATUS_OK,
        command=CMD_FORCE_GREEN_WAVE,
        message=f"Green wave activated on {target}: {len(affected)} intersections",
        data={"affected": affected, "duration_sec": green_wave_duration},
    )


def _handle_force_semaphore(
    query: MonitoringQuery,
    config,
    semaphore_push: zmq.Socket,
    db_primary_push: zmq.Socket,
    db_replica_push: zmq.Socket,
) -> MonitoringResponse:
    """Handle FORCE_SEMAPHORE: force a specific semaphore change."""
    intersection = query.interseccion
    new_state = query.new_state or SEMAPHORE_GREEN
    reason = query.reason or "user command"

    if not intersection:
        return MonitoringResponse(
            status="ERROR",
            command=CMD_FORCE_SEMAPHORE,
            message="Must specify 'interseccion' for force semaphore",
        )

    cmd = SemaphoreCommand(
        interseccion=intersection,
        new_state=new_state,
        reason=reason,
    )
    semaphore_push.send_string(cmd.to_json())

    # Determine resulting state for DB record
    result_ns = SEMAPHORE_GREEN if new_state == SEMAPHORE_GREEN else SEMAPHORE_RED
    result_ew = SEMAPHORE_RED if new_state == SEMAPHORE_GREEN else SEMAPHORE_GREEN

    state_envelope = make_semaphore_state_envelope(
        intersection, result_ns, result_ew, reason, config.normal_cycle_sec
    )
    db_primary_push.send_string(state_envelope)
    db_replica_push.send_string(state_envelope)

    logger.info(
        "[FORCE SEMAPHORE] %s -> %s (reason: %s)",
        intersection,
        new_state,
        reason,
    )

    return MonitoringResponse(
        status=STATUS_OK,
        command=CMD_FORCE_SEMAPHORE,
        message=f"Semaphore at {intersection} forced to {new_state}",
        data={"intersection": intersection, "new_state": new_state},
    )


def _handle_system_status(
    intersection_data: dict[str, dict],
    local_db: TrafficDB,
) -> MonitoringResponse:
    """Handle SYSTEM_STATUS: return overall system summary."""
    db_summary = local_db.get_system_summary()

    # Count current congested intersections
    congested = [
        k for k, v in intersection_data.items() if v["traffic_state"] == TRAFFIC_CONGESTION
    ]
    green_wave_active = [
        k for k, v in intersection_data.items() if v["traffic_state"] == TRAFFIC_GREEN_WAVE
    ]

    return MonitoringResponse(
        status=STATUS_OK,
        command=CMD_SYSTEM_STATUS,
        message="System status summary",
        data={
            "db_summary": db_summary,
            "currently_congested": congested,
            "currently_green_wave": green_wave_active,
            "total_intersections": len(intersection_data),
        },
    )


# ---------------------------------------------------------------------------
# Push to both databases helper
# ---------------------------------------------------------------------------


def push_to_dbs(
    db_primary_push: zmq.Socket,
    db_replica_push: zmq.Socket,
    envelope_json: str,
) -> None:
    """Send a DB write envelope to both primary and replica databases."""
    db_primary_push.send_string(envelope_json)
    db_replica_push.send_string(envelope_json)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_analytics_service(replica_db_path: str = "/data/traffic_replica.db") -> None:
    """
    Main analytics service loop.

    Polls the SUB socket (sensor events) and REP socket (monitoring queries)
    using zmq.Poller. Processes events, evaluates rules, and dispatches
    commands and data accordingly.

    Args:
        replica_db_path: Path to the local replica DB for monitoring queries.
    """
    config = get_config()
    intersection_data = init_intersection_data(config)

    # Local DB for answering monitoring queries directly
    local_db = TrafficDB(replica_db_path)

    context = zmq.Context()

    # --- SUB socket: receive sensor events from broker on PC1 ---
    sensor_sub = context.socket(zmq.SUB)
    broker_addr = config.zmq_address(config.pc1_host, "broker_pub")
    sensor_sub.connect(broker_addr)
    for topic in ALL_SENSOR_TOPICS:
        sensor_sub.setsockopt_string(zmq.SUBSCRIBE, topic)
    logger.info("SUB connected to broker at %s (topics: %s)", broker_addr, ALL_SENSOR_TOPICS)

    # --- REP socket: respond to monitoring queries from PC3 ---
    monitoring_rep = context.socket(zmq.REP)
    rep_bind = config.zmq_bind_address("analytics_rep")
    monitoring_rep.bind(rep_bind)
    logger.info("REP bound on %s (monitoring queries)", rep_bind)

    # --- PUSH socket: send commands to semaphore control (local) ---
    semaphore_push = context.socket(zmq.PUSH)
    sem_addr = f"tcp://127.0.0.1:{config.get_port('semaphore_control_pull')}"
    semaphore_push.connect(sem_addr)
    logger.info("PUSH connected to semaphore control at %s", sem_addr)

    # --- PUSH socket: send data to primary DB on PC3 ---
    db_primary_push = context.socket(zmq.PUSH)
    primary_addr = config.zmq_address(config.pc3_host, "db_primary_pull")
    db_primary_push.connect(primary_addr)
    logger.info("PUSH connected to primary DB at %s", primary_addr)

    # --- PUSH socket: send data to replica DB (local on PC2) ---
    db_replica_push = context.socket(zmq.PUSH)
    replica_addr = f"tcp://127.0.0.1:{config.get_port('db_replica_pull')}"
    db_replica_push.connect(replica_addr)
    logger.info("PUSH connected to replica DB at %s", replica_addr)

    # --- Poller ---
    poller = zmq.Poller()
    poller.register(sensor_sub, zmq.POLLIN)
    poller.register(monitoring_rep, zmq.POLLIN)

    logger.info("Analytics service started. Waiting for events...")

    event_count = 0
    decision_count = 0

    try:
        while _running:
            socks = dict(poller.poll(timeout=1000))

            # --- Handle sensor events ---
            if sensor_sub in socks and socks[sensor_sub] == zmq.POLLIN:
                message = sensor_sub.recv_string()
                try:
                    topic, payload = message.split(" ", 1)
                    event_data = from_json(payload)
                    event_count += 1

                    # Update per-intersection state
                    intersection = update_intersection_from_event(
                        intersection_data, topic, event_data
                    )

                    # Push raw event to both DBs
                    event_envelope = make_sensor_event_envelope(event_data, topic)
                    push_to_dbs(db_primary_push, db_replica_push, event_envelope)

                    # Also insert into local DB for query access
                    tipo_sensor = event_data.get("tipo_sensor", "unknown")
                    timestamp = event_data.get("timestamp") or event_data.get(
                        "timestamp_fin", now_iso()
                    )
                    local_db.insert_sensor_event(
                        sensor_id=event_data.get("sensor_id", "unknown"),
                        tipo_sensor=tipo_sensor,
                        interseccion=event_data.get("interseccion", "unknown"),
                        event_data=event_data,
                        timestamp=timestamp,
                    )

                    if intersection:
                        # Evaluate traffic rules
                        traffic_state, decision = evaluate_traffic_rules(
                            intersection_data, intersection, config
                        )

                        sensor_snapshot = {
                            "Q": intersection_data[intersection]["Q"],
                            "Vp": intersection_data[intersection]["Vp"],
                            "D": intersection_data[intersection]["D"],
                        }

                        # Log decision
                        logger.info(
                            "[EVENT #%d] %s @ %s -> state=%s, decision=%s (Q=%d, Vp=%.1f, D=%d)",
                            event_count,
                            event_data.get("sensor_id", "?"),
                            intersection,
                            traffic_state,
                            decision,
                            sensor_snapshot["Q"],
                            sensor_snapshot["Vp"],
                            sensor_snapshot["D"],
                        )

                        # If congestion detected, send semaphore command
                        if decision == DECISION_EXTEND_GREEN:
                            extension = config.congestion_extension_sec
                            total_cycle = config.normal_cycle_sec + extension

                            cmd = SemaphoreCommand(
                                interseccion=intersection,
                                new_state=SEMAPHORE_GREEN,
                                reason=f"congestion detected (Q={sensor_snapshot['Q']}, "
                                f"Vp={sensor_snapshot['Vp']}, D={sensor_snapshot['D']})",
                                duration_override_sec=total_cycle,
                            )
                            semaphore_push.send_string(cmd.to_json())
                            decision_count += 1

                            # Push semaphore state to DBs
                            state_envelope = make_semaphore_state_envelope(
                                intersection,
                                SEMAPHORE_GREEN,
                                SEMAPHORE_RED,
                                cmd.reason,
                                total_cycle,
                            )
                            push_to_dbs(db_primary_push, db_replica_push, state_envelope)

                        # Push congestion record to DBs (for all states, not just congestion)
                        congestion_envelope = make_congestion_envelope(
                            intersection, traffic_state, decision, sensor_snapshot
                        )
                        push_to_dbs(db_primary_push, db_replica_push, congestion_envelope)

                        # Also insert congestion record in local DB
                        local_db.insert_congestion_record(
                            interseccion=intersection,
                            traffic_state=traffic_state,
                            decision=decision,
                            details=f"Q={sensor_snapshot['Q']}, Vp={sensor_snapshot['Vp']}, "
                            f"D={sensor_snapshot['D']}",
                            sensor_data=sensor_snapshot,
                            timestamp=now_iso(),
                        )

                except ValueError:
                    logger.error("[ERROR] Could not parse message: %s", message[:200])
                except Exception as e:
                    logger.error("[ERROR] Failed to process sensor event: %s", e)

            # --- Handle monitoring queries ---
            if monitoring_rep in socks and socks[monitoring_rep] == zmq.POLLIN:
                query_msg = monitoring_rep.recv_string()
                try:
                    query_data = from_json(query_msg)
                    query = MonitoringQuery.from_dict(query_data)

                    logger.info("[QUERY] Received command: %s", query.command)

                    response = handle_monitoring_query(
                        query,
                        intersection_data,
                        local_db,
                        config,
                        semaphore_push,
                        db_primary_push,
                        db_replica_push,
                    )

                    monitoring_rep.send_string(response.to_json())
                    logger.info(
                        "[QUERY] Response: status=%s, message=%s",
                        response.status,
                        response.message,
                    )

                except Exception as e:
                    logger.error("[ERROR] Failed to handle monitoring query: %s", e)
                    error_resp = MonitoringResponse(
                        status="ERROR",
                        message=f"Internal error: {e}",
                    )
                    monitoring_rep.send_string(error_resp.to_json())

    except KeyboardInterrupt:
        logger.info("Analytics service interrupted.")
    finally:
        logger.info(
            "Analytics shutting down. Events: %d, Decisions: %d",
            event_count,
            decision_count,
        )
        local_db.close()
        sensor_sub.close()
        monitoring_rep.close()
        semaphore_push.close()
        db_primary_push.close()
        db_replica_push.close()
        context.term()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Analytics service for PC2")
    parser.add_argument(
        "--replica-db-path",
        default=os.environ.get("REPLICA_DB_PATH", "/data/traffic_replica.db"),
        help="Path to local replica DB for query access",
    )
    args = parser.parse_args(argv)
    run_analytics_service(replica_db_path=args.replica_db_path)


if __name__ == "__main__":
    main()
