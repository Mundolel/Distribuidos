"""
db_replica.py - Replica database worker for PC2.

Receives data from the analytics service via ZMQ PUSH/PULL and inserts it
into a local SQLite replica database. This replica serves as a backup in
case PC3 (primary DB) fails, ensuring uninterrupted system operation.

Communication pattern:
    Analytics (PUSH) --> db_replica (PULL) --> SQLite replica DB

The PULL socket binds on the configured port (db_replica_pull: 5564) and
waits for JSON envelope messages with a "type" field indicating the
record type to insert.

Envelope format:
    {"type": "sensor_event", "data": {...}}
    {"type": "congestion_record", "data": {...}}
    {"type": "semaphore_state", "data": {...}}
    {"type": "priority_action", "data": {...}}

Usage:
    python -m pc2.db_replica [--db-path /data/traffic_replica.db]
"""

import argparse
import json
import logging
import os
import signal

import zmq

from common.config_loader import get_config
from common.db_utils import TrafficDB

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("DB-Replica")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_running = True


def _signal_handler(sig, frame):
    global _running
    logger.info("Shutdown signal received, stopping DB replica worker...")
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

# ---------------------------------------------------------------------------
# Default DB path
# ---------------------------------------------------------------------------
DEFAULT_DB_PATH = os.environ.get("REPLICA_DB_PATH", "/data/traffic_replica.db")


# ---------------------------------------------------------------------------
# Envelope processing
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
# Main loop
# ---------------------------------------------------------------------------


def run_db_replica(db_path: str = DEFAULT_DB_PATH) -> None:
    """
    Main loop: bind PULL socket and insert received records into the replica DB.

    Args:
        db_path: Path to the SQLite replica database file.
    """
    config = get_config()
    db = TrafficDB(db_path)

    context = zmq.Context()
    pull_socket = context.socket(zmq.PULL)
    bind_addr = config.zmq_bind_address("db_replica_pull")
    pull_socket.bind(bind_addr)

    logger.info("DB Replica worker started | PULL bound on %s | DB: %s", bind_addr, db_path)

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
        logger.info("DB Replica worker interrupted.")
    finally:
        logger.info("DB Replica shutting down. Total records inserted: %d", record_count)
        db.close()
        pull_socket.close()
        context.term()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DB Replica worker for PC2")
    parser.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help=f"Path to replica SQLite database (default: {DEFAULT_DB_PATH})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    run_db_replica(db_path=args.db_path)


if __name__ == "__main__":
    main()
