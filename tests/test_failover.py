"""
test_failover.py - Unit tests for Phase 5 fault tolerance components.

Tests:
  - FailoverState: thread-safe state transitions and callbacks
  - HealthChecker: PING/PONG success, timeout counting, failover + recovery triggers
  - push_to_dbs: failover-aware DB write routing
  - ZMQ integration: health checker with a mock REP that stops responding
"""

import json
import threading
import time

import zmq

from common.constants import (
    HEALTH_CHECK_MSG,
    HEALTH_CHECK_RESPONSE,
)
from pc2.analytics_service import push_to_dbs
from pc2.health_checker import FailoverState, HealthChecker

# =============================================================================
# FailoverState Tests
# =============================================================================


class TestFailoverState:
    """Tests for the thread-safe FailoverState container."""

    def test_initial_state_is_alive(self):
        """PC3 should be alive by default."""
        state = FailoverState()
        assert state.is_pc3_alive() is True

    def test_set_failover(self):
        """After set_failover, PC3 should be reported as down."""
        state = FailoverState()
        state.set_failover()
        assert state.is_pc3_alive() is False

    def test_set_recovered(self):
        """After failover + recovery, PC3 should be alive again."""
        state = FailoverState()
        state.set_failover()
        assert state.is_pc3_alive() is False
        state.set_recovered()
        assert state.is_pc3_alive() is True

    def test_failover_callback_called_once(self):
        """on_failover callback should be called exactly once per transition."""
        calls = []
        state = FailoverState(on_failover=lambda: calls.append("failover"))
        state.set_failover()
        state.set_failover()  # second call - should NOT trigger callback
        assert calls == ["failover"]

    def test_recovery_callback_called_once(self):
        """on_recovery callback should be called exactly once per transition."""
        calls = []
        state = FailoverState(on_recovery=lambda: calls.append("recovery"))
        state.set_failover()
        state.set_recovered()
        state.set_recovered()  # second call - should NOT trigger callback
        assert calls == ["recovery"]

    def test_recovery_without_failover_no_callback(self):
        """Calling set_recovered when already alive should not trigger callback."""
        calls = []
        state = FailoverState(on_recovery=lambda: calls.append("recovery"))
        state.set_recovered()  # already alive
        assert calls == []

    def test_failover_recovery_cycle(self):
        """Multiple failover/recovery cycles should each trigger their callback."""
        failovers = []
        recoveries = []
        state = FailoverState(
            on_failover=lambda: failovers.append(1),
            on_recovery=lambda: recoveries.append(1),
        )
        for _ in range(3):
            state.set_failover()
            state.set_recovered()
        assert len(failovers) == 3
        assert len(recoveries) == 3

    def test_thread_safety(self):
        """Concurrent access should not corrupt state."""
        state = FailoverState()
        errors = []

        def toggle():
            try:
                for _ in range(100):
                    state.set_failover()
                    state.set_recovered()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=toggle) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # After all toggles complete, state should be alive
        assert state.is_pc3_alive() is True


# =============================================================================
# push_to_dbs failover-aware Tests
# =============================================================================


class TestPushToDbsFailover:
    """Tests that push_to_dbs correctly skips primary during failover."""

    def test_push_to_both_when_alive(self):
        """Both primary and replica should receive the message when PC3 is alive."""
        context = zmq.Context()
        port_primary = 18601
        port_replica = 18602

        pull_primary = context.socket(zmq.PULL)
        pull_primary.bind(f"tcp://127.0.0.1:{port_primary}")
        pull_replica = context.socket(zmq.PULL)
        pull_replica.bind(f"tcp://127.0.0.1:{port_replica}")

        push_primary = context.socket(zmq.PUSH)
        push_primary.connect(f"tcp://127.0.0.1:{port_primary}")
        push_replica = context.socket(zmq.PUSH)
        push_replica.connect(f"tcp://127.0.0.1:{port_replica}")

        time.sleep(0.3)

        state = FailoverState()  # alive by default
        envelope = json.dumps({"type": "sensor_event", "data": {"test": True}})

        # Wrap primary socket in a callable (matches new push_to_dbs API)
        def send_primary(msg):
            push_primary.send_string(msg)

        push_to_dbs(send_primary, push_replica, envelope, state)

        pull_primary.setsockopt(zmq.RCVTIMEO, 2000)
        pull_replica.setsockopt(zmq.RCVTIMEO, 2000)

        msg_primary = pull_primary.recv_string()
        msg_replica = pull_replica.recv_string()

        assert json.loads(msg_primary)["type"] == "sensor_event"
        assert json.loads(msg_replica)["type"] == "sensor_event"

        for s in [push_primary, push_replica, pull_primary, pull_replica]:
            s.close()
        context.term()

    def test_push_only_to_replica_during_failover(self):
        """Only replica should receive the message when PC3 is down."""
        context = zmq.Context()
        port_replica = 18603

        pull_replica = context.socket(zmq.PULL)
        pull_replica.bind(f"tcp://127.0.0.1:{port_replica}")

        push_replica = context.socket(zmq.PUSH)
        push_replica.connect(f"tcp://127.0.0.1:{port_replica}")

        time.sleep(0.3)

        state = FailoverState()
        state.set_failover()

        envelope = json.dumps({"type": "sensor_event", "data": {"test": True}})

        # primary is None during failover (socket was closed)
        push_to_dbs(None, push_replica, envelope, state)

        pull_replica.setsockopt(zmq.RCVTIMEO, 2000)
        msg_replica = pull_replica.recv_string()
        assert json.loads(msg_replica)["type"] == "sensor_event"

        for s in [push_replica, pull_replica]:
            s.close()
        context.term()

    def test_push_no_primary_when_primary_is_none(self):
        """When primary push socket is None, should not raise."""
        context = zmq.Context()
        port_replica = 18604

        pull_replica = context.socket(zmq.PULL)
        pull_replica.bind(f"tcp://127.0.0.1:{port_replica}")

        push_replica = context.socket(zmq.PUSH)
        push_replica.connect(f"tcp://127.0.0.1:{port_replica}")

        time.sleep(0.3)

        envelope = json.dumps({"type": "test", "data": {}})
        # No failover state, primary is None -- should not crash
        push_to_dbs(None, push_replica, envelope)

        pull_replica.setsockopt(zmq.RCVTIMEO, 2000)
        msg = pull_replica.recv_string()
        assert json.loads(msg)["type"] == "test"

        for s in [push_replica, pull_replica]:
            s.close()
        context.term()


# =============================================================================
# HealthChecker Tests
# =============================================================================


class TestHealthChecker:
    """Tests for the HealthChecker thread."""

    def test_ping_pong_success(self):
        """Health checker should detect PC3 as alive when REP responds."""
        context = zmq.Context()
        port = 18610
        state = FailoverState()
        stop = [False]  # mutable container for closure

        # Mock PC3 health check REP
        rep = context.socket(zmq.REP)
        rep.bind(f"tcp://127.0.0.1:{port}")

        # Patch config to use our test port and localhost
        class MockConfig:
            health_check_interval_sec = 1
            health_check_timeout_ms = 1000
            health_check_max_retries = 3
            pc3_host = "127.0.0.1"

            def zmq_address(self, host, port_name):
                return f"tcp://{host}:{port}"

            def get_port(self, name):
                return port

        checker = HealthChecker(
            context=context,
            state=state,
            config=MockConfig(),
            running=lambda: not stop[0],
        )
        checker.start()

        # Respond to pings for 2 rounds
        for _ in range(2):
            rep.setsockopt(zmq.RCVTIMEO, 3000)
            msg = rep.recv_string()
            assert msg == HEALTH_CHECK_MSG
            rep.send_string(HEALTH_CHECK_RESPONSE)

        # State should remain alive
        assert state.is_pc3_alive() is True

        stop[0] = True
        checker.join(timeout=5)
        rep.close()
        context.term()

    def test_failover_after_retries_exhausted(self):
        """Health checker should trigger failover after max retries with no response."""
        context = zmq.Context()
        port = 18611
        failover_events = []
        stop = [False]

        state = FailoverState(on_failover=lambda: failover_events.append(1))

        # NO REP socket -- pings will timeout
        class MockConfig:
            health_check_interval_sec = 0  # as fast as possible
            health_check_timeout_ms = 200  # short timeout
            health_check_max_retries = 2
            pc3_host = "127.0.0.1"

            def zmq_address(self, host, port_name):
                return f"tcp://{host}:{port}"

            def get_port(self, name):
                return port

        checker = HealthChecker(
            context=context,
            state=state,
            config=MockConfig(),
            running=lambda: not stop[0],
        )
        checker.start()

        # Wait enough time for 2+ failed pings (200ms timeout * 2 retries + buffer)
        time.sleep(2.0)

        assert state.is_pc3_alive() is False
        assert len(failover_events) == 1

        stop[0] = True
        checker.join(timeout=5)
        context.term()

    def test_recovery_after_failover(self):
        """Health checker should auto-recover when PC3 comes back."""
        context = zmq.Context()
        port = 18612
        recovery_events = []
        stop = [False]

        state = FailoverState(on_recovery=lambda: recovery_events.append(1))

        class MockConfig:
            health_check_interval_sec = 0
            health_check_timeout_ms = 300
            health_check_max_retries = 2
            pc3_host = "127.0.0.1"

            def zmq_address(self, host, port_name):
                return f"tcp://{host}:{port}"

            def get_port(self, name):
                return port

        checker = HealthChecker(
            context=context,
            state=state,
            config=MockConfig(),
            running=lambda: not stop[0],
        )
        checker.start()

        # Let it fail for a bit (no REP socket)
        time.sleep(1.5)
        assert state.is_pc3_alive() is False

        # Now start the REP socket (PC3 "comes back")
        rep = context.socket(zmq.REP)
        rep.bind(f"tcp://127.0.0.1:{port}")

        # Respond to pings until recovery is detected
        deadline = time.monotonic() + 5.0
        recovered = False
        while time.monotonic() < deadline and not recovered:
            if rep.poll(timeout=1000):
                rep.recv_string()
                rep.send_string(HEALTH_CHECK_RESPONSE)
                time.sleep(0.1)
                recovered = state.is_pc3_alive()

        assert recovered, "Health checker did not detect recovery"
        assert len(recovery_events) >= 1

        stop[0] = True
        checker.join(timeout=5)
        rep.close()
        context.term()


# =============================================================================
# ZMQ Integration: Full failover scenario
# =============================================================================


class TestFailoverIntegration:
    """Integration test simulating a full failover scenario."""

    def test_full_failover_and_recovery_scenario(self):
        """
        Simulate: PC3 alive -> PC3 dies -> FAILOVER -> PC3 recovers -> RECOVERY.
        Verify that push_to_dbs skips primary during failover window.
        """
        context = zmq.Context()
        hc_port = 18620
        events = {"failover": [], "recovery": []}
        stop = [False]

        state = FailoverState(
            on_failover=lambda: events["failover"].append(time.monotonic()),
            on_recovery=lambda: events["recovery"].append(time.monotonic()),
        )

        class MockConfig:
            health_check_interval_sec = 0
            health_check_timeout_ms = 200
            health_check_max_retries = 2
            pc3_host = "127.0.0.1"

            def zmq_address(self, host, port_name):
                return f"tcp://{host}:{hc_port}"

            def get_port(self, name):
                return hc_port

        # Phase 1: PC3 is alive
        rep = context.socket(zmq.REP)
        rep.bind(f"tcp://127.0.0.1:{hc_port}")

        checker = HealthChecker(
            context=context,
            state=state,
            config=MockConfig(),
            running=lambda: not stop[0],
        )
        checker.start()

        # Respond to a couple of pings
        for _ in range(2):
            if rep.poll(timeout=2000):
                rep.recv_string()
                rep.send_string(HEALTH_CHECK_RESPONSE)

        assert state.is_pc3_alive() is True

        # Phase 2: PC3 dies (close REP socket)
        rep.close()
        time.sleep(1.5)  # let health checker fail enough times
        assert state.is_pc3_alive() is False
        assert len(events["failover"]) == 1

        # Phase 3: PC3 recovers (rebind REP)
        rep = context.socket(zmq.REP)
        rep.bind(f"tcp://127.0.0.1:{hc_port}")

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not state.is_pc3_alive():
            if rep.poll(timeout=500):
                rep.recv_string()
                rep.send_string(HEALTH_CHECK_RESPONSE)

        assert state.is_pc3_alive() is True
        assert len(events["recovery"]) >= 1

        stop[0] = True
        checker.join(timeout=5)
        rep.close()
        context.term()
