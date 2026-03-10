"""
health_checker.py - Health check module for detecting PC3 failures.

Implements the "Lazy Pirate" REQ pattern to periodically ping the health
check REP socket on PC3's db_primary process.  If the ping fails for N
consecutive attempts the system enters FAILOVER mode.  When PC3 responds
again, the system auto-recovers.

Components:
    FailoverState  - Thread-safe shared state (pc3_alive flag).
    HealthChecker  - Daemon thread that performs periodic PING/PONG checks.

Usage (inside analytics_service.py):
    state = FailoverState()
    checker = HealthChecker(zmq_context, state, config)
    checker.start()        # launches daemon thread
    ...
    if state.is_pc3_alive():
        push_to_primary(...)

The FailoverState exposes two callbacks that analytics can register:
    on_failover  - called once when transitioning to FAILOVER
    on_recovery  - called once when transitioning back to ALIVE
"""

import logging
import threading
import time
from collections.abc import Callable

import zmq

from common.config_loader import CityConfig
from common.constants import (
    HEALTH_CHECK_MSG,
    HEALTH_CHECK_RESPONSE,
)

logger = logging.getLogger("HealthChecker")

# ---------------------------------------------------------------------------
# Thread-safe failover state
# ---------------------------------------------------------------------------


class FailoverState:
    """
    Thread-safe container for the PC3-alive flag.

    The health checker thread writes to it; the analytics main loop reads it.
    Optional callbacks are invoked exactly once per state transition.
    """

    def __init__(
        self,
        on_failover: Callable[[], None] | None = None,
        on_recovery: Callable[[], None] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._pc3_alive = True
        self._on_failover = on_failover
        self._on_recovery = on_recovery

    # -- read (called from main loop, cheap) --

    def is_pc3_alive(self) -> bool:
        with self._lock:
            return self._pc3_alive

    # -- write (called from health-checker thread) --

    def set_failover(self) -> None:
        """Transition to FAILOVER.  Calls on_failover callback once."""
        with self._lock:
            if self._pc3_alive:
                self._pc3_alive = False
                logger.critical("[FAILOVER] PC3 is down. Using replica DB on PC2.")
                if self._on_failover:
                    try:
                        self._on_failover()
                    except Exception:
                        logger.exception("on_failover callback error")

    def set_recovered(self) -> None:
        """Transition back to ALIVE.  Calls on_recovery callback once."""
        with self._lock:
            if not self._pc3_alive:
                self._pc3_alive = True
                logger.info("[RECOVERY] PC3 is back. Resuming writes to primary DB.")
                if self._on_recovery:
                    try:
                        self._on_recovery()
                    except Exception:
                        logger.exception("on_recovery callback error")


# ---------------------------------------------------------------------------
# Health checker thread (Lazy Pirate pattern)
# ---------------------------------------------------------------------------


class HealthChecker(threading.Thread):
    """
    Daemon thread that periodically pings PC3's health-check REP socket.

    Uses the *Lazy Pirate* pattern: after every send, wait for a reply with
    a timeout.  On timeout, close the REQ socket and recreate it (required
    because ZMQ REQ enters a broken state after an unanswered send).

    Args:
        context:   Shared ZMQ context.
        state:     FailoverState instance (written by this thread).
        config:    CityConfig instance.
        running:   External stop flag (e.g. the module-level ``_running``).
    """

    def __init__(
        self,
        context: zmq.Context,
        state: FailoverState,
        config: CityConfig,
        running: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(name="HealthChecker", daemon=True)
        self._zmq_ctx = context
        self._state = state
        self._config = config
        self._running = running or (lambda: True)

        # Parameters from config
        self._interval = config.health_check_interval_sec
        self._timeout_ms = config.health_check_timeout_ms
        self._max_retries = config.health_check_max_retries

        # Build target address
        self._target_addr = config.zmq_address(config.pc3_host, "health_check_rep")

        self._consecutive_failures = 0

    # -- helpers --

    def _create_req_socket(self) -> zmq.Socket:
        """Create a fresh REQ socket with the configured timeout."""
        sock = self._zmq_ctx.socket(zmq.REQ)
        sock.setsockopt(zmq.RCVTIMEO, self._timeout_ms)
        sock.setsockopt(zmq.LINGER, 0)
        sock.connect(self._target_addr)
        return sock

    # -- main loop --

    def run(self) -> None:
        logger.info(
            "Health checker started | target=%s | interval=%ds | timeout=%dms | max_retries=%d",
            self._target_addr,
            self._interval,
            self._timeout_ms,
            self._max_retries,
        )

        req: zmq.Socket | None = None

        try:
            while self._running():
                # (Re)create REQ socket if needed (Lazy Pirate reset)
                if req is None:
                    req = self._create_req_socket()

                success = self._ping(req)

                if success:
                    self._consecutive_failures = 0
                    self._state.set_recovered()
                else:
                    self._consecutive_failures += 1
                    logger.warning(
                        "[HEALTH] PING failed (%d/%d)",
                        self._consecutive_failures,
                        self._max_retries,
                    )
                    # Lazy Pirate: close broken REQ and recreate next iter
                    req.close()
                    req = None

                    if self._consecutive_failures >= self._max_retries:
                        self._state.set_failover()

                # Wait before next check
                # Use short sleeps so we can react to _running going False
                deadline = time.monotonic() + self._interval
                while self._running() and time.monotonic() < deadline:
                    time.sleep(0.25)

        except Exception:
            logger.exception("Health checker crashed")
        finally:
            if req is not None:
                req.close()
            logger.info("Health checker stopped.")

    def _ping(self, req: zmq.Socket) -> bool:
        """
        Send PING and wait for PONG.

        Returns:
            True if PONG received, False on timeout or error.
        """
        try:
            req.send_string(HEALTH_CHECK_MSG)
            reply = req.recv_string()
            if reply == HEALTH_CHECK_RESPONSE:
                logger.debug("[HEALTH] PONG received from PC3")
                return True
            logger.warning("[HEALTH] Unexpected reply: %s", reply)
            return False
        except zmq.Again:
            # Timeout -- no reply
            return False
        except zmq.ZMQError as e:
            logger.error("[HEALTH] ZMQ error: %s", e)
            return False
