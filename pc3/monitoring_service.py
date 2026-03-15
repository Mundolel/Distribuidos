"""
monitoring_service.py - Interactive monitoring and query CLI for PC3.

Provides a user-facing command-line interface to:
  1. Query the current state of a specific intersection
  2. Query congestion history between timestamps
  3. Force a green wave on a row or column (e.g., ambulance priority)
  4. Force a specific semaphore state change
  5. View overall system status
  6. Run a health check against the analytics service

Communicates with the analytics service on PC2 via ZMQ REQ/REP pattern.
All operations and responses are printed to stdout as required by the
project specification.

The shared menu, command handlers, and response formatters live in
common/monitoring_commands.py so they can be reused by the PC2 fallback
monitoring CLI without cross-PC import dependencies.

Usage:
    python -m pc3.monitoring_service
"""

import logging
import signal
import sys

import zmq

from common.config_loader import get_config
from common.monitoring_commands import (
    MENU,
    do_force_green_wave,
    do_force_semaphore,
    do_health_check,
    do_query_history,
    do_query_intersection,
    do_system_status,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("Monitoring")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_running = True


def _signal_handler(sig, frame):
    global _running
    logger.info("Shutdown signal received, stopping monitoring service...")
    _running = False
    sys.exit(0)


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

# REQ socket receive timeout (ms) - avoid hanging if PC2 is down
REQ_TIMEOUT_MS = 10000


# ---------------------------------------------------------------------------
# Main CLI loop
# ---------------------------------------------------------------------------


def run_monitoring_cli() -> None:
    """
    Main interactive CLI loop for the monitoring service.

    Connects to the analytics service REP socket on PC2 and presents
    a menu of operations to the user.
    """
    config = get_config()
    context = zmq.Context()

    # REQ socket to analytics service on PC2
    req_socket = context.socket(zmq.REQ)
    analytics_addr = config.zmq_address(config.pc2_host, "analytics_rep")
    req_socket.connect(analytics_addr)
    req_socket.setsockopt(zmq.RCVTIMEO, REQ_TIMEOUT_MS)
    req_socket.setsockopt(zmq.LINGER, 0)

    logger.info("Monitoring service started | REQ connected to %s", analytics_addr)
    print(f"\n  Connected to analytics service at {analytics_addr}")

    try:
        while _running:
            print(MENU)
            try:
                choice = input("  Select option: ").strip()
            except EOFError:
                break

            if choice == "1":
                do_query_intersection(req_socket)
            elif choice == "2":
                do_query_history(req_socket)
            elif choice == "3":
                do_force_green_wave(req_socket)
            elif choice == "4":
                do_force_semaphore(req_socket)
            elif choice == "5":
                do_system_status(req_socket)
            elif choice == "6":
                do_health_check(req_socket)
            elif choice == "0":
                print("\n  Exiting monitoring service. Goodbye.")
                break
            else:
                print(f"\n  Invalid option: {choice}")

    except KeyboardInterrupt:
        print("\n\n  Monitoring service interrupted.")
    finally:
        logger.info("Monitoring service shutting down.")
        req_socket.close()
        context.term()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    run_monitoring_cli()


if __name__ == "__main__":
    main()
