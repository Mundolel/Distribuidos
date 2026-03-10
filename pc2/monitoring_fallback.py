"""
monitoring_fallback.py - Fallback monitoring CLI that runs on PC2.

When PC3 is down, the normal monitoring service (which runs on PC3) is
unavailable.  This fallback allows a user to connect to the PC2 container
and still query the analytics service via the same interactive CLI.

The only difference from the normal monitoring service is that the REQ
socket connects to localhost (127.0.0.1) instead of the Docker hostname
"pc2", since this process runs on PC2 itself.

Usage (from inside the PC2 container):
    python -m pc2.monitoring_fallback

Or via docker exec:
    docker exec -it pc2-analytics python -m pc2.monitoring_fallback
"""

import logging

import zmq

from common.config_loader import get_config
from pc3.monitoring_service import (
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
logger = logging.getLogger("Monitoring-Fallback")

# REQ socket receive timeout (ms)
REQ_TIMEOUT_MS = 10000


def run_fallback_cli() -> None:
    """
    Run the monitoring CLI on PC2 (fallback mode).

    Connects to analytics REP on localhost instead of the Docker 'pc2' host,
    then reuses the same menu and command handlers from monitoring_service.
    """
    config = get_config()
    context = zmq.Context()

    # Connect to analytics on localhost (we ARE on PC2)
    port = config.get_port("analytics_rep")
    analytics_addr = f"tcp://127.0.0.1:{port}"

    req_socket = context.socket(zmq.REQ)
    req_socket.connect(analytics_addr)
    req_socket.setsockopt(zmq.RCVTIMEO, REQ_TIMEOUT_MS)
    req_socket.setsockopt(zmq.LINGER, 0)

    logger.info("Fallback monitoring started | REQ connected to %s", analytics_addr)
    print(f"\n  [FALLBACK MODE] Connected to analytics at {analytics_addr}")
    print("  PC3 is down -- using replica DB on PC2")

    try:
        while True:
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
                print("\n  Exiting fallback monitoring. Goodbye.")
                break
            else:
                print(f"\n  Invalid option: {choice}")

    except KeyboardInterrupt:
        print("\n\n  Fallback monitoring interrupted.")
    finally:
        logger.info("Fallback monitoring shutting down.")
        req_socket.close()
        context.term()


def main() -> None:
    run_fallback_cli()


if __name__ == "__main__":
    main()
