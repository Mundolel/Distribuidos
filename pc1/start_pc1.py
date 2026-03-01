"""
start_pc1.py - Entrypoint for PC1 (Sensors + Broker).

Launches all PC1 processes as subprocesses:
  1. Broker (standard or threaded mode)
  2. Camera sensor process (all cameras from config)
  3. Inductive loop sensor process (all inductive loops from config)
  4. GPS sensor process (all GPS sensors from config)

The broker starts first and waits briefly before sensors start publishing,
to ensure ZMQ connections are established.

Usage:
    python pc1/start_pc1.py [--broker-mode standard|threaded] [--interval 10]

Environment variables:
    BROKER_MODE: "standard" or "threaded" (default: standard)
    SENSOR_INTERVAL: sensor event interval in seconds (default: from config)
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
logger = logging.getLogger("PC1-Launcher")

# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------
_processes: list[subprocess.Popen] = []


def _shutdown(signum=None, frame=None):
    """Terminate all child processes gracefully."""
    logger.info("Shutting down all PC1 processes...")
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

    logger.info("All PC1 processes stopped.")
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
    parser = argparse.ArgumentParser(description="PC1 Launcher: Sensors + Broker")
    parser.add_argument(
        "--broker-mode",
        choices=["standard", "threaded"],
        default=os.environ.get("BROKER_MODE", "standard"),
        help="Broker mode (default: standard, env: BROKER_MODE)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("SENSOR_INTERVAL", "0")),
        help="Sensor interval override in seconds (0 = use config default)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    python = sys.executable

    logger.info("=" * 60)
    logger.info("PC1 LAUNCHER - Sensors & Broker")
    logger.info("Broker mode: %s", args.broker_mode)
    logger.info("=" * 60)

    # 1. Start broker first
    broker_cmd = [python, "-m", "pc1.broker", "--mode", args.broker_mode]
    _launch(broker_cmd, "Broker")

    # Give broker time to bind its sockets
    time.sleep(1.0)

    # 2. Build sensor commands
    interval_args = []
    if args.interval > 0:
        interval_args = ["--interval", str(args.interval)]

    # Camera sensors
    camera_cmd = [python, "-m", "pc1.sensors.camera_sensor", "--all", *interval_args]
    _launch(camera_cmd, "Camera Sensors")

    # Inductive loop sensors
    inductive_cmd = [python, "-m", "pc1.sensors.inductive_sensor", "--all", *interval_args]
    _launch(inductive_cmd, "Inductive Sensors")

    # GPS sensors
    gps_cmd = [python, "-m", "pc1.sensors.gps_sensor", "--all", *interval_args]
    _launch(gps_cmd, "GPS Sensors")

    logger.info("All PC1 processes launched (%d total).", len(_processes))

    # 3. Monitor processes - restart crashed ones or exit if all die
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
                logger.error("All PC1 processes have exited. Shutting down.")
                break
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown()


if __name__ == "__main__":
    main()
