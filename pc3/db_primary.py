"""
db_primary.py - Primary database worker for PC3.

Receives data from the analytics service (PC2) via ZMQ PUSH/PULL and inserts
it into the primary SQLite database. This is the main persistence layer for
the traffic management system.

Additionally, a background daemon thread binds a health check REP socket
(port 5565) that responds to "PING" with "PONG". This is used by PC2 in
Phase 5 to detect if PC3 is alive and trigger failover if needed.

Communication patterns:
    Analytics (PUSH) --> db_primary (PULL) --> SQLite primary DB
    PC2 health checker (REQ) --> db_primary health thread (REP)

The PULL socket binds on the configured port (db_primary_pull: 5563).
The health check REP socket binds on (health_check_rep: 5565).

Envelope format (same as db_replica.py on PC2):
    {"type": "sensor_event", "data": {...}}
    {"type": "congestion_record", "data": {...}}
    {"type": "semaphore_state", "data": {...}}
    {"type": "priority_action", "data": {...}}

Usage:
    python -m pc3.db_primary [--db-path /data/traffic_primary.db]
"""

import argparse
import json
import logging
import os
import signal
import threading

import zmq

from common.config_loader import get_config
from common.constants import HEALTH_CHECK_MSG, HEALTH_CHECK_RESPONSE
from common.db_utils import TrafficDB

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("DB-Primary")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_running = True


def _signal_handler(sig, frame):
    global _running
    logger.info("Shutdown signal received, stopping DB primary worker...")
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

# ---------------------------------------------------------------------------
# Default DB path
# ---------------------------------------------------------------------------
DEFAULT_DB_PATH = os.environ.get("PRIMARY_DB_PATH", "/data/traffic_primary.db")


# ---------------------------------------------------------------------------
# Envelope processing (duplicated from pc2/db_replica.py by design)
# ---------------------------------------------------------------------------


def process_envelope(db: TrafficDB, envelope: dict) -> None:
    """
    Process a single DB write envelope and insert the record.

    Args:
        db: TrafficDB instance to insert into.
        envelope: Dict with 'type' and 'data' fields.
    """
    record_type = envelope.get("type", "")
    data = envelope.get("data", {})

    if record_type == "sensor_event":
        db.insert_sensor_event(
            sensor_id=data["sensor_id"],
            tipo_sensor=data["tipo_sensor"],
            interseccion=data["interseccion"],
            event_data=data.get("event_data", {}),
            timestamp=data["timestamp"],
        )
        logger.info(
            "[INSERT sensor_event] %s @ %s (%s)",
            data["sensor_id"],
            data["interseccion"],
            data["tipo_sensor"],
        )

    elif record_type == "congestion_record":
        db.insert_congestion_record(
            interseccion=data["interseccion"],
            traffic_state=data["traffic_state"],
            decision=data["decision"],
            details=data.get("details", ""),
            sensor_data=data.get("sensor_data"),
            timestamp=data["timestamp"],
        )
        logger.info(
            "[INSERT congestion_record] %s state=%s decision=%s",
            data["interseccion"],
            data["traffic_state"],
            data["decision"],
        )

    elif record_type == "semaphore_state":
        db.insert_semaphore_state(
            interseccion=data["interseccion"],
            state_ns=data["state_ns"],
            state_ew=data["state_ew"],
            reason=data.get("reason", ""),
            cycle_duration_sec=data.get("cycle_duration_sec", 15),
            timestamp=data["timestamp"],
        )
        logger.info(
            "[INSERT semaphore_state] %s NS=%s EW=%s",
            data["interseccion"],
            data["state_ns"],
            data["state_ew"],
        )

    elif record_type == "priority_action":
        db.insert_priority_action(
            action_type=data["action_type"],
            target=data["target"],
            reason=data.get("reason", ""),
            requested_by=data.get("requested_by", "system"),
            affected_intersections=data.get("affected_intersections", []),
            timestamp=data["timestamp"],
        )
        logger.info(
            "[INSERT priority_action] %s target=%s",
            data["action_type"],
            data["target"],
        )

    else:
        logger.warning("[UNKNOWN] Received unknown record type: %s", record_type)


# ---------------------------------------------------------------------------
# Health check background thread
# ---------------------------------------------------------------------------


def _health_check_thread(context: zmq.Context) -> None:
    """
    Background daemon thread that responds to health check pings from PC2.

    Binds a REP socket on the health_check_rep port (5565) and replies
    "PONG" to incoming "PING" messages. PC2's analytics service uses this
    to detect if PC3 is alive.
    """
    config = get_config()
    rep_socket = context.socket(zmq.REP)
    bind_addr = config.zmq_bind_address("health_check_rep")
    rep_socket.bind(bind_addr)

    logger.info("[HealthCheck] REP bound on %s (responds to PING with PONG)", bind_addr)

    try:
        while _running:
            if rep_socket.poll(timeout=1000):
                message = rep_socket.recv_string()
                logger.debug("[HealthCheck] Received: %s", message)
                if message == HEALTH_CHECK_MSG:
                    rep_socket.send_string(HEALTH_CHECK_RESPONSE)
                else:
                    logger.warning("[HealthCheck] Unexpected message: %s", message)
                    rep_socket.send_string("ERROR")
    except zmq.ZMQError as e:
        if _running:
            logger.error("[HealthCheck] ZMQ error: %s", e)
    finally:
        logger.info("[HealthCheck] Thread shutting down.")
        rep_socket.close()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_db_primary(db_path: str = DEFAULT_DB_PATH) -> None:
    """
    Main loop: bind PULL socket and insert received records into the primary DB.
    Also starts the health check background thread.

    Args:
        db_path: Path to the SQLite primary database file.
    """
    config = get_config()
    db = TrafficDB(db_path)

    context = zmq.Context()

    # Start health check thread
    hc_thread = threading.Thread(
        target=_health_check_thread,
        args=(context,),
        name="HealthCheck",
        daemon=True,
    )
    hc_thread.start()

    # Main PULL socket for DB writes
    pull_socket = context.socket(zmq.PULL)
    bind_addr = config.zmq_bind_address("db_primary_pull")
    pull_socket.bind(bind_addr)

    logger.info("DB Primary worker started | PULL bound on %s | DB: %s", bind_addr, db_path)

    record_count = 0
    try:
        while _running:
            if pull_socket.poll(timeout=1000):
                message = pull_socket.recv_string()
                try:
                    envelope = json.loads(message)
                    process_envelope(db, envelope)
                    record_count += 1
                except json.JSONDecodeError:
                    logger.error("[ERROR] Invalid JSON received: %s", message[:200])
                except KeyError as e:
                    logger.error("[ERROR] Missing field in envelope: %s", e)
                except Exception as e:
                    logger.error("[ERROR] Failed to process envelope: %s", e)
    except KeyboardInterrupt:
        logger.info("DB Primary worker interrupted.")
    finally:
        logger.info("DB Primary shutting down. Total records inserted: %d", record_count)
        db.close()
        pull_socket.close()
        context.term()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DB Primary worker for PC3")
    parser.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help=f"Path to primary SQLite database (default: {DEFAULT_DB_PATH})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    run_db_primary(db_path=args.db_path)


if __name__ == "__main__":
    main()
