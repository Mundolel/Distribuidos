"""
broker.py - ZMQ Broker for sensor data ingestion and forwarding.

The broker acts as a message intermediary on PC1. It:
  1. SUBscribes to all sensor PUB sockets (camera, inductive, GPS) on localhost
  2. Forwards every received event by PUBlishing it on a single port for PC2

This decouples sensor producers from the analytics consumer on PC2.

Communication pattern:
    Sensors (PUB) --[topic msg]--> Broker (SUB) --[topic msg]--> Broker (PUB) --> PC2 Analytics (SUB)

Usage:
    python -m pc1.broker [--mode standard]
"""

import argparse
import logging
import signal
import time

import zmq

from common.config_loader import get_config

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("Broker")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_running = True


def _signal_handler(sig, frame):
    global _running
    logger.info("Shutdown signal received, stopping broker...")
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ---------------------------------------------------------------------------
# Standard (single-threaded) broker
# ---------------------------------------------------------------------------


def run_broker_standard() -> None:
    """
    Standard single-threaded broker.

    Uses zmq.Poller to listen on multiple SUB sockets (one per sensor type)
    and forwards all received messages to a single PUB socket for PC2.
    """
    config = get_config()
    context = zmq.Context()

    # -- PUB socket: forwards events to PC2 --
    publisher = context.socket(zmq.PUB)
    pub_bind = config.zmq_bind_address("broker_pub")
    publisher.bind(pub_bind)
    logger.info("Broker PUB socket bound on %s", pub_bind)

    # -- SUB sockets: subscribe to each sensor type on localhost --
    subscribers = []
    sensor_ports = [
        ("sensor_camera_pub", "camara"),
        ("sensor_inductive_pub", "espira"),
        ("sensor_gps_pub", "gps"),
    ]

    poller = zmq.Poller()

    for port_name, topic in sensor_ports:
        sub = context.socket(zmq.SUB)
        addr = f"tcp://127.0.0.1:{config.get_port(port_name)}"
        sub.connect(addr)
        # Subscribe to the specific topic
        sub.setsockopt_string(zmq.SUBSCRIBE, topic)
        poller.register(sub, zmq.POLLIN)
        subscribers.append(sub)
        logger.info("Broker SUB connected to %s (topic: %s)", addr, topic)

    logger.info("Broker started in STANDARD mode. Forwarding sensor events to PC2...")

    # Small delay for connections to establish
    time.sleep(0.5)

    event_count = 0
    try:
        while _running:
            # Poll with 1s timeout so we can check _running flag
            socks = dict(poller.poll(timeout=1000))
            for sub in subscribers:
                if sub in socks and socks[sub] == zmq.POLLIN:
                    message = sub.recv_string()
                    # Forward the message as-is (topic + payload)
                    publisher.send_string(message)
                    event_count += 1

                    # Extract topic for logging
                    topic = message.split(" ", 1)[0]
                    logger.info(
                        "[FORWARD #%d] topic=%s | size=%d bytes",
                        event_count,
                        topic,
                        len(message),
                    )
    except KeyboardInterrupt:
        logger.info("Broker interrupted.")
    finally:
        logger.info("Broker shutting down. Total events forwarded: %d", event_count)
        for sub in subscribers:
            sub.close()
        publisher.close()
        context.term()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ZMQ Broker for sensor data")
    parser.add_argument(
        "--mode",
        choices=["standard", "threaded"],
        default="standard",
        help="Broker mode: standard (single-thread) or threaded (multi-thread)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.mode == "threaded":
        # Import threaded variant to avoid circular dependencies at module level
        from pc1.broker_threaded import run_broker_threaded

        run_broker_threaded()
    else:
        run_broker_standard()


if __name__ == "__main__":
    main()
