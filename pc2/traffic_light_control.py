"""
traffic_light_control.py - Semaphore control service for PC2.

Manages the state of all simulated semaphores in the city grid. Receives
commands from the analytics service via ZMQ PUSH/PULL and updates the
in-memory state of each intersection's traffic lights.

Each intersection has two directions:
    - NS (North-South)
    - EW (East-West)
When one direction is GREEN, the other must be RED (no yellow transition).

Communication pattern:
    Analytics (PUSH) --> traffic_light_control (PULL)

The PULL socket binds on the configured port (semaphore_control_pull: 5562).

Command format (JSON):
    {
        "interseccion": "INT-B3",
        "new_state": "GREEN",       # Desired state for the congested direction
        "reason": "congestion detected",
        "duration_override_sec": 25,
        "timestamp": "2026-..."
    }

Usage:
    python -m pc2.traffic_light_control
"""

import json
import logging
import signal
import time

import zmq

from common.config_loader import get_config
from common.constants import SEMAPHORE_GREEN, SEMAPHORE_RED
from common.models import SemaphoreCommand, SemaphoreState, now_iso

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("SemaphoreControl")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_running = True


def _signal_handler(sig, frame):
    global _running
    logger.info("Shutdown signal received, stopping semaphore control...")
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ---------------------------------------------------------------------------
# Semaphore state management
# ---------------------------------------------------------------------------


def init_semaphore_states(config) -> dict[str, SemaphoreState]:
    """
    Initialize all semaphores to default state.

    Default: NS=RED, EW=GREEN for all intersections.
    This means East-West traffic flows first on system startup.

    Args:
        config: CityConfig instance.

    Returns:
        Dictionary mapping intersection IDs to their SemaphoreState.
    """
    states = {}
    normal_cycle = config.normal_cycle_sec

    for intersection in config.intersections:
        states[intersection] = SemaphoreState(
            interseccion=intersection,
            state_ns=SEMAPHORE_RED,
            state_ew=SEMAPHORE_GREEN,
            cycle_duration_sec=normal_cycle,
        )

    logger.info(
        "Initialized %d semaphores (default: NS=RED, EW=GREEN, cycle=%ds)",
        len(states),
        normal_cycle,
    )
    return states


def apply_command(
    states: dict[str, SemaphoreState],
    command: SemaphoreCommand,
    config,
) -> SemaphoreState | None:
    """
    Apply a semaphore command to update the intersection state.

    A command with new_state="GREEN" toggles the intersection:
    the current RED direction becomes GREEN and vice versa.

    A command with new_state="RED" sets NS=RED, EW=GREEN (reset to default).

    Args:
        states: Current semaphore states dictionary.
        command: The semaphore command to apply.
        config: CityConfig instance.

    Returns:
        Updated SemaphoreState, or None if intersection not found.
    """
    intersection = command.interseccion

    if intersection not in states:
        logger.warning("[UNKNOWN] Intersection %s not found in semaphore states", intersection)
        return None

    old_state = states[intersection]
    old_ns = old_state.state_ns
    old_ew = old_state.state_ew

    # Determine cycle duration
    cycle_sec = command.duration_override_sec or config.normal_cycle_sec

    if command.new_state == SEMAPHORE_GREEN:
        # Toggle: swap NS and EW states
        new_ns = SEMAPHORE_GREEN if old_ns == SEMAPHORE_RED else SEMAPHORE_RED
        new_ew = SEMAPHORE_RED if old_ew == SEMAPHORE_GREEN else SEMAPHORE_GREEN
    else:
        # Reset to default (NS=RED, EW=GREEN)
        new_ns = SEMAPHORE_RED
        new_ew = SEMAPHORE_GREEN

    # Update state
    new_state = SemaphoreState(
        interseccion=intersection,
        state_ns=new_ns,
        state_ew=new_ew,
        last_change=now_iso(),
        cycle_duration_sec=cycle_sec,
    )
    states[intersection] = new_state

    # Latency measurement (cross-process wall-clock comparison)
    if command.created_at:
        latency_ms = (time.time() - command.created_at) * 1000
        logger.info("[LATENCY] %s: %.2f ms", intersection, latency_ms)

    # Log the change
    logger.info(
        "[%s] NS: %s->%s, EW: %s->%s (reason: %s, cycle: %ds)",
        intersection,
        old_ns,
        new_ns,
        old_ew,
        new_ew,
        command.reason or "unspecified",
        cycle_sec,
    )

    return new_state


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_traffic_light_control() -> None:
    """
    Main loop: bind PULL socket and process semaphore commands from analytics.
    """
    config = get_config()
    states = init_semaphore_states(config)

    context = zmq.Context()
    pull_socket = context.socket(zmq.PULL)
    bind_addr = config.zmq_bind_address("semaphore_control_pull")
    pull_socket.bind(bind_addr)

    logger.info("Semaphore Control started | PULL bound on %s", bind_addr)

    command_count = 0
    try:
        while _running:
            if pull_socket.poll(timeout=1000):
                message = pull_socket.recv_string()
                try:
                    data = json.loads(message)
                    command = SemaphoreCommand.from_dict(data)
                    result = apply_command(states, command, config)
                    if result:
                        command_count += 1
                except json.JSONDecodeError:
                    logger.error("[ERROR] Invalid JSON received: %s", message[:200])
                except Exception as e:
                    logger.error("[ERROR] Failed to process command: %s", e)
    except KeyboardInterrupt:
        logger.info("Semaphore Control interrupted.")
    finally:
        logger.info(
            "Semaphore Control shutting down. Total commands processed: %d",
            command_count,
        )
        pull_socket.close()
        context.term()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    run_traffic_light_control()


if __name__ == "__main__":
    main()
