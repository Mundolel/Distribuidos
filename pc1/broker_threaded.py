"""
broker_threaded.py - Multithreaded ZMQ Broker variant for performance comparison.

Each sensor topic subscription runs in its own thread. All threads forward
messages to a shared inproc PUSH socket which a collector thread reads from
and publishes externally. This avoids ZMQ socket sharing across threads
(which is not thread-safe).

Architecture:
    Thread-1 (SUB camara)  --> PUSH inproc://broker_pipe
    Thread-2 (SUB espira)  --> PUSH inproc://broker_pipe
    Thread-3 (SUB gps)     --> PUSH inproc://broker_pipe
                                     |
    Collector (PULL inproc) <--------+---> PUB tcp://*:5560

This design is compared against the standard single-threaded broker for
the performance experiments required by the project specification.

Usage:
    python -m pc1.broker --mode threaded
"""

import logging
import signal
import threading
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
logger = logging.getLogger("BrokerThreaded")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_running = True


def _signal_handler(sig, frame):
    global _running
    logger.info("Shutdown signal received, stopping threaded broker...")
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

# Inproc address for inter-thread communication
INPROC_ADDR = "inproc://broker_pipe"


# ---------------------------------------------------------------------------
# Worker thread: subscribes to one sensor topic, pushes to inproc
# ---------------------------------------------------------------------------


def _subscriber_worker(
    context: zmq.Context,
    port: int,
    topic: str,
    thread_name: str,
) -> None:
    """
    Worker thread that subscribes to a single sensor PUB port and forwards
    messages to the inproc PUSH socket for the collector.
    """
    # SUB socket to receive from sensor
    sub = context.socket(zmq.SUB)
    addr = f"tcp://127.0.0.1:{port}"
    sub.connect(addr)
    sub.setsockopt_string(zmq.SUBSCRIBE, topic)

    # PUSH socket to forward to collector
    push = context.socket(zmq.PUSH)
    push.connect(INPROC_ADDR)

    logger.info("[%s] SUB connected to %s (topic: %s)", thread_name, addr, topic)

    count = 0
    try:
        while _running:
            # Poll with timeout so thread can check _running flag
            if sub.poll(timeout=1000):
                message = sub.recv_string()
                push.send_string(message)
                count += 1
                logger.debug("[%s] Forwarded message #%d", thread_name, count)
    except zmq.ZMQError as e:
        if _running:
            logger.error("[%s] ZMQ error: %s", thread_name, e)
    finally:
        logger.info("[%s] Shutting down. Messages forwarded: %d", thread_name, count)
        sub.close()
        push.close()


# ---------------------------------------------------------------------------
# Collector thread: PULL from inproc, PUB to PC2
# ---------------------------------------------------------------------------


def _collector_worker(context: zmq.Context, pub_bind: str) -> None:
    """
    Collector thread that pulls messages from all subscriber worker threads
    and publishes them to the external PUB socket for PC2.
    """
    # PULL socket from worker threads
    pull = context.socket(zmq.PULL)
    pull.bind(INPROC_ADDR)

    # PUB socket to PC2
    pub = context.socket(zmq.PUB)
    pub.bind(pub_bind)

    logger.info("[Collector] PULL bound on %s, PUB bound on %s", INPROC_ADDR, pub_bind)

    event_count = 0
    try:
        while _running:
            if pull.poll(timeout=1000):
                message = pull.recv_string()
                pub.send_string(message)
                event_count += 1
                topic = message.split(" ", 1)[0]
                logger.info(
                    "[FORWARD #%d] topic=%s | size=%d bytes",
                    event_count,
                    topic,
                    len(message),
                )
    except zmq.ZMQError as e:
        if _running:
            logger.error("[Collector] ZMQ error: %s", e)
    finally:
        logger.info("[Collector] Shutting down. Total events: %d", event_count)
        pull.close()
        pub.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_broker_threaded() -> None:
    """
    Launch the multithreaded broker: one subscriber thread per sensor topic
    plus a collector thread that publishes to PC2.
    """
    config = get_config()
    context = zmq.Context()

    pub_bind = config.zmq_bind_address("broker_pub")

    sensor_ports = [
        (config.get_port("sensor_camera_pub"), "camara", "Thread-Camera"),
        (config.get_port("sensor_inductive_pub"), "espira", "Thread-Inductive"),
        (config.get_port("sensor_gps_pub"), "gps", "Thread-GPS"),
    ]

    logger.info("Starting broker in THREADED mode with %d worker threads...", len(sensor_ports))

    # Start collector thread first (it binds inproc)
    collector = threading.Thread(
        target=_collector_worker,
        args=(context, pub_bind),
        name="Collector",
        daemon=True,
    )
    collector.start()

    # Small delay for inproc bind to take effect
    time.sleep(0.2)

    # Start subscriber worker threads
    workers = []
    for port, topic, name in sensor_ports:
        t = threading.Thread(
            target=_subscriber_worker,
            args=(context, port, topic, name),
            name=name,
            daemon=True,
        )
        t.start()
        workers.append(t)

    logger.info("Threaded broker fully started. %d workers + 1 collector.", len(workers))

    # Main thread waits for shutdown signal
    try:
        while _running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        global _running
        _running = False
        logger.info("Waiting for threads to finish...")
        for t in workers:
            t.join(timeout=3)
        collector.join(timeout=3)
        context.term()
        logger.info("Threaded broker shut down.")


if __name__ == "__main__":
    run_broker_threaded()
