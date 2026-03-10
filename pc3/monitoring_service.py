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

Usage:
    python -m pc3.monitoring_service
"""

import logging
import signal
import sys

import zmq

from common.config_loader import get_config
from common.constants import (
    CMD_FORCE_GREEN_WAVE,
    CMD_FORCE_SEMAPHORE,
    CMD_HEALTH_CHECK,
    CMD_QUERY_HISTORY,
    CMD_QUERY_INTERSECTION,
    CMD_SYSTEM_STATUS,
)
from common.models import MonitoringQuery, MonitoringResponse, from_json

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
# Display helpers
# ---------------------------------------------------------------------------

MENU = """
============================================================
       TRAFFIC MONITORING SYSTEM - Command Center
============================================================
  1. Query intersection status
  2. Query congestion history
  3. Force green wave (emergency)
  4. Force semaphore change
  5. System status
  6. Health check
  0. Exit
============================================================"""


def print_separator():
    print("-" * 60)


def print_response_header(title: str):
    print()
    print_separator()
    print(f"  {title}")
    print_separator()


def format_intersection_response(resp: MonitoringResponse) -> None:
    """Pretty-print a QUERY_INTERSECTION response."""
    if resp.status != "OK":
        print(f"  ERROR: {resp.message}")
        return

    data = resp.data or {}
    intersection = data.get("intersection", "?")
    readings = data.get("current_readings", {})
    semaphore = data.get("semaphore_state")
    event_count = data.get("recent_events_count", 0)

    print_response_header(f"Intersection {intersection}")
    print(f"  Traffic State : {readings.get('traffic_state', '?')}")
    print(f"  Queue (Q)     : {readings.get('Q', '?')}")
    print(f"  Avg Speed (Vp): {readings.get('Vp', '?')} km/h")
    print(f"  Density (D)   : {readings.get('D', '?')}")

    if semaphore:
        print(
            f"  Semaphore     : NS={semaphore.get('state_ns', '?')}, "
            f"EW={semaphore.get('state_ew', '?')}"
        )
        print(f"  Cycle Duration: {semaphore.get('cycle_duration_sec', '?')}s")
    else:
        print("  Semaphore     : (no state recorded)")

    print(f"  Last Updated  : {readings.get('last_updated', '?')}")
    print(f"  Recent Events : {event_count}")
    print_separator()


def format_history_response(resp: MonitoringResponse) -> None:
    """Pretty-print a QUERY_HISTORY response."""
    if resp.status != "OK":
        print(f"  ERROR: {resp.message}")
        return

    data = resp.data or {}
    records = data.get("records", [])

    print_response_header(f"Congestion History ({len(records)} records)")
    if not records:
        print("  (no records found)")
    else:
        for rec in records:
            ts = rec.get("timestamp", "?")
            inter = rec.get("interseccion", "?")
            state = rec.get("traffic_state", "?")
            decision = rec.get("decision", "?")
            details = rec.get("details", "")
            print(f"  [{ts}] {inter} | {state:<12} | {decision:<14} | {details}")
    print_separator()


def format_green_wave_response(resp: MonitoringResponse) -> None:
    """Pretty-print a FORCE_GREEN_WAVE response."""
    if resp.status != "OK":
        print(f"  ERROR: {resp.message}")
        return

    data = resp.data or {}
    affected = data.get("affected", [])
    duration = data.get("duration_sec", "?")

    print_response_header("Green Wave Activated")
    print(f"  Affected Intersections: {', '.join(affected)}")
    print(f"  Duration: {duration}s")
    print_separator()


def format_semaphore_response(resp: MonitoringResponse) -> None:
    """Pretty-print a FORCE_SEMAPHORE response."""
    if resp.status != "OK":
        print(f"  ERROR: {resp.message}")
        return

    data = resp.data or {}
    print_response_header("Semaphore Change Applied")
    print(f"  Intersection: {data.get('intersection', '?')}")
    print(f"  New State   : {data.get('new_state', '?')}")
    print_separator()


def format_system_status_response(resp: MonitoringResponse) -> None:
    """Pretty-print a SYSTEM_STATUS response."""
    if resp.status not in ("OK", "FAILOVER"):
        print(f"  ERROR: {resp.message}")
        return

    data = resp.data or {}
    summary = data.get("db_summary", {})
    congested = data.get("currently_congested", [])
    green_wave = data.get("currently_green_wave", [])
    total = data.get("total_intersections", "?")
    pc3_alive = data.get("pc3_alive", True)

    print_response_header("System Status")
    if not pc3_alive:
        print("  *** FAILOVER MODE - PC3 is DOWN, using replica DB ***")
    print(f"  Total Sensor Events    : {summary.get('total_sensor_events', '?')}")
    print(f"  Congestion Detections  : {summary.get('total_congestion_detections', '?')}")
    print(f"  Green Waves            : {summary.get('total_green_waves', '?')}")
    print(f"  Semaphore Changes      : {summary.get('total_semaphore_changes', '?')}")
    print(f"  Currently Congested    : {', '.join(congested) if congested else '(none)'}")
    print(f"  Active Green Waves     : {', '.join(green_wave) if green_wave else '(none)'}")
    print(f"  Total Intersections    : {total}")
    print(f"  PC3 Status             : {'ALIVE' if pc3_alive else 'DOWN (FAILOVER)'}")
    print_separator()


def format_health_check_response(resp: MonitoringResponse) -> None:
    """Pretty-print a HEALTH_CHECK response."""
    print_response_header("Health Check")
    if resp.status == "OK":
        print(f"  Analytics Service: OK ({resp.message})")
    else:
        print(f"  Analytics Service: FAILED ({resp.message})")
    print_separator()


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------


def send_query(req_socket: zmq.Socket, query: MonitoringQuery) -> MonitoringResponse | None:
    """
    Send a monitoring query and receive the response.

    Returns:
        MonitoringResponse on success, None on timeout or error.
    """
    try:
        logger.info("[SEND] command=%s", query.command)
        req_socket.send_string(query.to_json())
        resp_str = req_socket.recv_string()
        resp_data = from_json(resp_str)
        response = MonitoringResponse.from_dict(resp_data)
        logger.info("[RECV] status=%s, message=%s", response.status, response.message)
        return response
    except zmq.Again:
        print("\n  [TIMEOUT] No response from analytics service (PC2 may be down)")
        logger.warning("[TIMEOUT] No response from analytics service")
        return None
    except Exception as e:
        print(f"\n  [ERROR] Communication error: {e}")
        logger.error("[ERROR] Communication error: %s", e)
        return None


def do_query_intersection(req_socket: zmq.Socket) -> None:
    """Prompt user and query a specific intersection."""
    intersection = input("  Intersection ID (e.g., INT-A1): ").strip().upper()
    if not intersection:
        print("  Cancelled.")
        return

    query = MonitoringQuery(command=CMD_QUERY_INTERSECTION, interseccion=intersection)
    resp = send_query(req_socket, query)
    if resp:
        format_intersection_response(resp)


def do_query_history(req_socket: zmq.Socket) -> None:
    """Prompt user and query congestion history."""
    intersection = input("  Intersection (Enter for all): ").strip().upper() or None
    ts_start = input("  Start time (ISO, Enter to skip): ").strip() or None
    ts_end = input("  End time (ISO, Enter to skip): ").strip() or None

    query = MonitoringQuery(
        command=CMD_QUERY_HISTORY,
        interseccion=intersection,
        timestamp_inicio=ts_start,
        timestamp_fin=ts_end,
    )
    resp = send_query(req_socket, query)
    if resp:
        format_history_response(resp)


def do_force_green_wave(req_socket: zmq.Socket) -> None:
    """Prompt user and force a green wave on a row or column."""
    row = input("  Row letter (A-D, Enter to skip): ").strip().upper() or None
    col_str = input("  Column number (1-4, Enter to skip): ").strip()
    column = int(col_str) if col_str else None
    reason = input("  Reason (e.g., ambulance): ").strip() or "emergency"

    if not row and not column:
        print("  Must specify a row or column. Cancelled.")
        return

    query = MonitoringQuery(
        command=CMD_FORCE_GREEN_WAVE,
        row=row,
        column=column,
        reason=reason,
    )
    resp = send_query(req_socket, query)
    if resp:
        format_green_wave_response(resp)


def do_force_semaphore(req_socket: zmq.Socket) -> None:
    """Prompt user and force a specific semaphore change."""
    intersection = input("  Intersection ID (e.g., INT-B3): ").strip().upper()
    if not intersection:
        print("  Cancelled.")
        return

    new_state = input("  New state (GREEN/RED) [GREEN]: ").strip().upper() or "GREEN"
    if new_state not in ("GREEN", "RED"):
        print(f"  Invalid state: {new_state}. Must be GREEN or RED.")
        return

    reason = input("  Reason: ").strip() or "user command"

    query = MonitoringQuery(
        command=CMD_FORCE_SEMAPHORE,
        interseccion=intersection,
        new_state=new_state,
        reason=reason,
    )
    resp = send_query(req_socket, query)
    if resp:
        format_semaphore_response(resp)


def do_system_status(req_socket: zmq.Socket) -> None:
    """Query and display overall system status."""
    query = MonitoringQuery(command=CMD_SYSTEM_STATUS)
    resp = send_query(req_socket, query)
    if resp:
        format_system_status_response(resp)


def do_health_check(req_socket: zmq.Socket) -> None:
    """Run a health check against the analytics service."""
    query = MonitoringQuery(command=CMD_HEALTH_CHECK)
    resp = send_query(req_socket, query)
    if resp:
        format_health_check_response(resp)


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
