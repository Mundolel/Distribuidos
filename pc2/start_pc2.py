"""
start_pc2.py - Entrypoint for PC2 (Analytics + Semaphore Control + DB Replica).

Launches all PC2 processes as subprocesses:
  1. DB Replica worker (binds PULL on :5564 - must start first)
  2. Semaphore Control service (binds PULL on :5562 - must start before analytics)
  3. Analytics Service (connects to all sockets - starts last)

The launch order ensures that PULL sockets are bound before the analytics
service tries to connect its PUSH sockets to them.

Usage:
    python pc2/start_pc2.py [--replica-db-path /data/traffic_replica.db]

Environment variables:
    REPLICA_DB_PATH: path to replica SQLite database (default: /data/traffic_replica.db)
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("PC2-Launcher")

# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------
_processes: list[subprocess.Popen] = []


def _shutdown(signum=None, frame=None):
    """Terminate all child processes gracefully."""
    logger.info("Shutting down all PC2 processes...")
    for proc in _processes:
        if proc.poll() is None:
            logger.info("Terminating PID %d (%s)", proc.pid, proc.args)
            proc.terminate()

    # Wait for graceful shutdown, then force-kill
    for proc in _processes:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Force-killing PID %d", proc.pid)
            proc.kill()

    logger.info("All PC2 processes stopped.")
    sys.exit(0)


signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)


def _launch(cmd: list[str], name: str) -> subprocess.Popen:
    """Launch a subprocess and register it for cleanup."""
    logger.info("Starting %s: %s", name, " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    _processes.append(proc)
    logger.info("%s started (PID %d)", name, proc.pid)
    return proc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PC2 Launcher: Analytics + Control + Replica")
    parser.add_argument(
        "--replica-db-path",
        default=os.environ.get("REPLICA_DB_PATH", "/data/traffic_replica.db"),
        help="Path to replica SQLite database (default: /data/traffic_replica.db)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    python = sys.executable

    logger.info("=" * 60)
    logger.info("PC2 LAUNCHER - Analytics, Semaphore Control & DB Replica")
    logger.info("Replica DB path: %s", args.replica_db_path)
    logger.info("=" * 60)

    # 1. Start DB Replica first (it binds PULL on :5564)
    replica_cmd = [python, "-m", "pc2.db_replica", "--db-path", args.replica_db_path]
    _launch(replica_cmd, "DB Replica")

    # 2. Start Semaphore Control (it binds PULL on :5562)
    semaphore_cmd = [python, "-m", "pc2.traffic_light_control"]
    _launch(semaphore_cmd, "Semaphore Control")

    # Give PULL sockets time to bind before analytics connects
    time.sleep(1.0)

    # 3. Start Analytics Service (connects to all sockets)
    analytics_cmd = [
        python,
        "-m",
        "pc2.analytics_service",
        "--replica-db-path",
        args.replica_db_path,
    ]
    _launch(analytics_cmd, "Analytics Service")

    logger.info("All PC2 processes launched (%d total).", len(_processes))
    logger.info(
        "If PC3 fails, use the fallback monitoring CLI:\n"
        "  docker exec -it pc2-analytics python -m pc2.monitoring_fallback"
    )

    # 4. Monitor processes
    try:
        while True:
            time.sleep(2)
            for proc in _processes:
                retcode = proc.poll()
                if retcode is not None:
                    logger.warning(
                        "Process PID %d (%s) exited with code %d",
                        proc.pid,
                        proc.args,
                        retcode,
                    )
            # Check if all processes have died
            if all(p.poll() is not None for p in _processes):
                logger.error("All PC2 processes have exited. Shutting down.")
                break
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown()


if __name__ == "__main__":
    main()
