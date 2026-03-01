"""
camera_sensor.py - Camera sensor simulator for traffic monitoring.

Generates EVENTO_LONGITUD_COLA (Lq) events: queue length and average speed
at an intersection. Publishes events via ZMQ PUB socket on topic "camara".

Supports two modes:
  - Single-sensor: one sensor_id + intersection via CLI args
  - Multi-sensor: reads all cameras from config, cycles through them on
    a single PUB socket (used by start_pc1.py)

Usage:
    # Single sensor:
    python -m pc1.sensors.camera_sensor --sensor-id CAM-A1 --intersection INT-A1

    # All sensors from config:
    python -m pc1.sensors.camera_sensor --all
"""

import argparse
import logging
import random
import signal
import time

import zmq

from common.config_loader import get_config
from common.constants import (
    CAMERA_SPEED_MAX,
    CAMERA_SPEED_MIN,
    CAMERA_VOLUME_MAX,
    CAMERA_VOLUME_MIN,
    SENSOR_DEFAULT_INTERVAL_SEC,
    TOPIC_CAMERA,
)
from common.models import CameraEvent

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("CameraSensor")

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


def generate_camera_event(sensor_id: str, intersection: str) -> CameraEvent:
    """Generate a random camera event simulating queue length and speed."""
    volumen = random.randint(CAMERA_VOLUME_MIN, CAMERA_VOLUME_MAX)
    velocidad = round(random.uniform(CAMERA_SPEED_MIN, CAMERA_SPEED_MAX), 1)
    return CameraEvent(
        sensor_id=sensor_id,
        interseccion=intersection,
        volumen=volumen,
        velocidad_promedio=velocidad,
    )


def run_camera_sensor(
    sensors: list[dict[str, str]],
    interval_sec: float,
    pub_port: int,
) -> None:
    """
    Main loop: generate camera events for one or more sensors and publish
    them over a single ZMQ PUB socket.

    In each cycle, one event is generated per sensor. The delay between
    individual sensor events within a cycle is interval / len(sensors)
    to spread them evenly.

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
        "Camera sensor process started | sensors=%s | interval=%ss | PUB on %s",
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
                event = generate_camera_event(
                    sensor_def["sensor_id"], sensor_def["interseccion"]
                )
                message = f"{TOPIC_CAMERA} {event.to_json()}"
                publisher.send_string(message)
                logger.info(
                    "[%s @ %s] volumen=%d, velocidad=%.1f km/h",
                    event.sensor_id,
                    event.interseccion,
                    event.volumen,
                    event.velocidad_promedio,
                )
                time.sleep(per_sensor_delay)
    except KeyboardInterrupt:
        logger.info("Camera sensor process interrupted.")
    finally:
        logger.info("Camera sensor process shutting down.")
        publisher.close()
        context.term()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Camera sensor simulator")
    parser.add_argument("--sensor-id", help="Sensor identifier (e.g. CAM-A1)")
    parser.add_argument("--intersection", help="Intersection ID (e.g. INT-A1)")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all camera sensors from config",
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
    pub_port = args.port if args.port else config.get_port("sensor_camera_pub")

    if args.all:
        sensors = config.cameras
    elif args.sensor_id and args.intersection:
        sensors = [{"sensor_id": args.sensor_id, "interseccion": args.intersection}]
    else:
        logger.error("Provide either --all or both --sensor-id and --intersection")
        return

    run_camera_sensor(
        sensors=sensors,
        interval_sec=args.interval,
        pub_port=pub_port,
    )


if __name__ == "__main__":
    main()
