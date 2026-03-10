"""
gps_sensor.py - GPS sensor simulator for traffic monitoring.

Generates EVENTO_DENSIDAD_DE_TRAFICO (Dt) events: average speed and
congestion level derived from it. Publishes events via ZMQ PUB socket
on topic "gps".

Congestion levels (auto-calculated by GPSEvent):
    - ALTA: velocidad_promedio < 10 km/h
    - NORMAL: 11 <= velocidad_promedio <= 39 km/h
    - BAJA: velocidad_promedio > 40 km/h

Supports two modes:
  - Single-sensor: one sensor_id + intersection via CLI args
  - Multi-sensor: reads all GPS sensors from config, cycles through them

Usage:
    python -m pc1.sensors.gps_sensor --sensor-id GPS-A1 --intersection INT-A1
    python -m pc1.sensors.gps_sensor --all
"""

import argparse
import logging
import random
import signal
import time

import zmq

from common.config_loader import get_config
from common.constants import (
    GPS_SPEED_MAX,
    GPS_SPEED_MIN,
    SENSOR_DEFAULT_INTERVAL_SEC,
    TOPIC_GPS,
)
from common.models import GPSEvent

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("GPSSensor")

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


def generate_gps_event(sensor_id: str, intersection: str) -> GPSEvent:
    """
    Generate a random GPS event simulating traffic density.

    The congestion level is automatically derived from velocidad_promedio
    by the GPSEvent.__post_init__ method.
    """
    velocidad = round(random.uniform(GPS_SPEED_MIN, GPS_SPEED_MAX), 1)
    return GPSEvent(
        sensor_id=sensor_id,
        interseccion=intersection,
        velocidad_promedio=velocidad,
    )


def run_gps_sensor(
    sensors: list[dict[str, str]],
    interval_sec: float,
    pub_port: int,
) -> None:
    """
    Main loop: generate GPS events for one or more sensors and publish
    them over a single ZMQ PUB socket.

    Args:
        sensors: List of sensor dicts with 'sensor_id' and 'interseccion'.
        interval_sec: Seconds between full cycles of event generation.
        pub_port: ZMQ PUB port to bind on.
    """
    context = zmq.Context()
    publisher = context.socket(zmq.PUB)
    bind_addr = f"tcp://*:{pub_port}"
    publisher.bind(bind_addr)

    sensor_ids = [s["sensor_id"] for s in sensors]
    logger.info(
        "GPS sensor process started | sensors=%s | interval=%ss | PUB on %s",
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
                event = generate_gps_event(sensor_def["sensor_id"], sensor_def["interseccion"])
                message = f"{TOPIC_GPS} {event.to_json()}"
                publisher.send_string(message)
                logger.info(
                    "[%s @ %s] velocidad=%.1f km/h, congestion=%s",
                    event.sensor_id,
                    event.interseccion,
                    event.velocidad_promedio,
                    event.nivel_congestion,
                )
                time.sleep(per_sensor_delay)
    except KeyboardInterrupt:
        logger.info("GPS sensor process interrupted.")
    finally:
        logger.info("GPS sensor process shutting down.")
        publisher.close()
        context.term()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GPS sensor simulator")
    parser.add_argument("--sensor-id", help="Sensor identifier (e.g. GPS-A1)")
    parser.add_argument("--intersection", help="Intersection ID (e.g. INT-A1)")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all GPS sensors from config",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=SENSOR_DEFAULT_INTERVAL_SEC,
        help=f"Event generation interval in seconds (default: {SENSOR_DEFAULT_INTERVAL_SEC})",
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
    pub_port = args.port if args.port else config.get_port("sensor_gps_pub")

    if args.all:
        sensors = config.gps_sensors
    elif args.sensor_id and args.intersection:
        sensors = [{"sensor_id": args.sensor_id, "interseccion": args.intersection}]
    else:
        logger.error("Provide either --all or both --sensor-id and --intersection")
        return

    run_gps_sensor(
        sensors=sensors,
        interval_sec=args.interval,
        pub_port=pub_port,
    )


if __name__ == "__main__":
    main()
