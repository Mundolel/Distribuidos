"""
inductive_sensor.py - Inductive loop sensor simulator for traffic monitoring.

Generates EVENTO_CONTEO_VEHICULAR (Cv) events: vehicle count over a time
interval. Publishes events via ZMQ PUB socket on topic "espira".

The inductive loop counts vehicles passing over it during a fixed interval
(default 30s, matching semaphore cycle times).

Supports two modes:
  - Single-sensor: one sensor_id + intersection via CLI args
  - Multi-sensor: reads all inductive loops from config, cycles through them

Usage:
    python -m pc1.sensors.inductive_sensor --sensor-id ESP-A2 --intersection INT-A2
    python -m pc1.sensors.inductive_sensor --all
"""

import argparse
import logging
import random
import signal
import time
from datetime import UTC, datetime, timedelta

import zmq

from common.config_loader import get_config
from common.constants import (
    INDUCTIVE_COUNT_MAX,
    INDUCTIVE_COUNT_MIN,
    INDUCTIVE_INTERVAL_SEC,
    TOPIC_INDUCTIVE,
)
from common.models import InductiveEvent, now_iso

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("InductiveSensor")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_running = True


def _signal_handler(sig, frame):
    global _running
    logger.info("Shutdown signal received, stopping sensor...")
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ---------------------------------------------------------------------------
# Sensor logic
# ---------------------------------------------------------------------------


def generate_inductive_event(
    sensor_id: str, intersection: str, interval_sec: int
) -> InductiveEvent:
    """Generate a random inductive loop event simulating vehicle count."""
    vehiculos = random.randint(INDUCTIVE_COUNT_MIN, INDUCTIVE_COUNT_MAX)
    fin_dt = datetime.now(UTC)
    inicio_dt = fin_dt - timedelta(seconds=interval_sec)
    ts_inicio = inicio_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    ts_fin = fin_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    return InductiveEvent(
        sensor_id=sensor_id,
        interseccion=intersection,
        vehiculos_contados=vehiculos,
        intervalo_segundos=interval_sec,
        timestamp_inicio=ts_inicio,
        timestamp_fin=ts_fin,
    )


def run_inductive_sensor(
    sensors: list[dict[str, str]],
    interval_sec: int,
    pub_port: int,
) -> None:
    """
    Main loop: generate inductive loop events for one or more sensors
    and publish them over a single ZMQ PUB socket.

    Args:
        sensors: List of sensor dicts with 'sensor_id' and 'interseccion'.
        interval_sec: Measurement interval in seconds (default 30).
        pub_port: ZMQ PUB port to bind on.
    """
    context = zmq.Context()
    publisher = context.socket(zmq.PUB)
    bind_addr = f"tcp://*:{pub_port}"
    publisher.bind(bind_addr)

    sensor_ids = [s["sensor_id"] for s in sensors]
    logger.info(
        "Inductive sensor process started | sensors=%s | interval=%ds | PUB on %s",
        sensor_ids,
        interval_sec,
        bind_addr,
    )

    # Small delay so subscribers can connect (ZMQ slow-joiner problem)
    time.sleep(0.5)

    # Spread events evenly within each cycle
    per_sensor_delay = interval_sec / max(len(sensors), 1)

    try:
        while _running:
            for sensor_def in sensors:
                if not _running:
                    break
                event = generate_inductive_event(
                    sensor_def["sensor_id"],
                    sensor_def["interseccion"],
                    interval_sec,
                )
                message = f"{TOPIC_INDUCTIVE} {event.to_json()}"
                publisher.send_string(message)
                logger.info(
                    "[%s @ %s] vehiculos_contados=%d, intervalo=%ds",
                    event.sensor_id,
                    event.interseccion,
                    event.vehiculos_contados,
                    event.intervalo_segundos,
                )
                time.sleep(per_sensor_delay)
    except KeyboardInterrupt:
        logger.info("Inductive sensor process interrupted.")
    finally:
        logger.info("Inductive sensor process shutting down.")
        publisher.close()
        context.term()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inductive loop sensor simulator")
    parser.add_argument("--sensor-id", help="Sensor identifier (e.g. ESP-A2)")
    parser.add_argument("--intersection", help="Intersection ID (e.g. INT-A2)")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all inductive loop sensors from config",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=INDUCTIVE_INTERVAL_SEC,
        help=f"Measurement interval in seconds (default: {INDUCTIVE_INTERVAL_SEC})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="ZMQ PUB port (default: from config)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    config = get_config()
    pub_port = args.port if args.port else config.get_port("sensor_inductive_pub")

    if args.all:
        sensors = config.inductive_loops
    elif args.sensor_id and args.intersection:
        sensors = [{"sensor_id": args.sensor_id, "interseccion": args.intersection}]
    else:
        logger.error("Provide either --all or both --sensor-id and --intersection")
        return

    run_inductive_sensor(
        sensors=sensors,
        interval_sec=args.interval,
        pub_port=pub_port,
    )


if __name__ == "__main__":
    main()
