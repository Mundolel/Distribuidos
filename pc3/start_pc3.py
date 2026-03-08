"""
start_pc3.py - Entrypoint for PC3 (Primary DB + Monitoring Service).

Launches PC3 processes:
  1. DB Primary worker as a background subprocess (binds PULL :5563 + health REP :5565)
  2. Monitoring service in the foreground (gets stdin for interactive CLI)

The monitoring service runs in the main process so it can receive user input
via stdin. The docker-compose configuration sets stdin_open and tty for this.

Usage:
    python pc3/start_pc3.py [--db-path /data/traffic_primary.db]

Environment variables:
    PRIMARY_DB_PATH: path to primary SQLite database (default: /data/traffic_primary.db)
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
logger = logging.getLogger("PC3-Launcher")

# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------
_bg_process: subprocess.Popen | None = None


def _shutdown(signum=None, frame=None):
    """Terminate the background DB process gracefully."""
    global _bg_process
    logger.info("Shutting down PC3 processes...")
    if _bg_process and _bg_process.poll() is None:
        logger.info("Terminating DB Primary (PID %d)", _bg_process.pid)
        _bg_process.terminate()
        try:
            _bg_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Force-killing DB Primary (PID %d)", _bg_process.pid)
            _bg_process.kill()
    logger.info("All PC3 processes stopped.")
    sys.exit(0)


signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PC3 Launcher: Primary DB + Monitoring")
    parser.add_argument(
        "--db-path",
        default=os.environ.get("PRIMARY_DB_PATH", "/data/traffic_primary.db"),
        help="Path to primary SQLite database (default: /data/traffic_primary.db)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    global _bg_process
    args = parse_args(argv)
    python = sys.executable

    logger.info("=" * 60)
    logger.info("PC3 LAUNCHER - Primary DB & Monitoring Service")
    logger.info("Primary DB path: %s", args.db_path)
    logger.info("=" * 60)

    # 1. Start DB Primary as background subprocess
    db_cmd = [python, "-m", "pc3.db_primary", "--db-path", args.db_path]
    logger.info("Starting DB Primary: %s", " ".join(db_cmd))
    _bg_process = subprocess.Popen(
        db_cmd,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    logger.info("DB Primary started (PID %d)", _bg_process.pid)

    # Give PULL + health check sockets time to bind
    time.sleep(1.0)

    # 2. Run monitoring service in the foreground (gets stdin)
    logger.info("Starting Monitoring Service in foreground...")
    try:
        from pc3.monitoring_service import main as monitoring_main

        monitoring_main()
    except KeyboardInterrupt:
        logger.info("Monitoring interrupted.")
    except Exception as e:
        logger.error("Monitoring service error: %s", e)
    finally:
        _shutdown()


if __name__ == "__main__":
    main()
