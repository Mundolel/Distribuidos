"""
run_scenarios.py - Performance test scenario runner.

Orchestrates the 4 experiment scenarios from Table 1 of the project spec,
collecting throughput (DB inserts in 2 minutes) and latency (command-to-state-change
time) measurements for each scenario.

Scenarios:
    1A: 1 sensor/type, 10s interval, standard broker
    1B: 1 sensor/type, 10s interval, threaded broker
    2A: 2 sensors/type, 5s interval, standard broker
    2B: 2 sensors/type, 5s interval, threaded broker

Usage:
    python -m perf.run_scenarios [--duration 120] [--output benchmark_results]

Requirements:
    - Docker Compose must be available
    - The system images must be built (`docker compose build`)
"""

import argparse
import json
import logging
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("PerfRunner")

# ---------------------------------------------------------------------------
# Scenario definitions (from project spec Table 1)
# ---------------------------------------------------------------------------
SCENARIOS = [
    {"name": "1A", "sensor_count": 1, "interval": 10, "broker_mode": "standard"},
    {"name": "1B", "sensor_count": 1, "interval": 10, "broker_mode": "threaded"},
    {"name": "2A", "sensor_count": 2, "interval": 5, "broker_mode": "standard"},
    {"name": "2B", "sensor_count": 2, "interval": 5, "broker_mode": "threaded"},
]

# Regex pattern for latency log lines from traffic_light_control
# Example: "[LATENCY] INT-B3: 12.34 ms"
LATENCY_PATTERN = re.compile(r"\[LATENCY\]\s+([\w-]+):\s+([\d.]+)\s+ms")


# ---------------------------------------------------------------------------
# Docker Compose helpers
# ---------------------------------------------------------------------------


def _run_compose(args: list[str], env: dict | None = None, check: bool = True) -> str:
    """Run a docker compose command and return stdout."""
    cmd = ["docker", "compose", *args]
    merged_env = {**os.environ, **(env or {})}
    logger.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=merged_env,
        check=check,
    )
    if result.returncode != 0 and check:
        logger.error("Command failed: %s\nstderr: %s", " ".join(cmd), result.stderr)
    return result.stdout


def _compose_up(scenario: dict) -> None:
    """Start the full system with scenario-specific environment variables."""
    env = {
        "BROKER_MODE": scenario["broker_mode"],
        "SENSOR_INTERVAL": str(scenario["interval"]),
        "SENSOR_COUNT": str(scenario["sensor_count"]),
    }
    logger.info(
        "Starting system: broker=%s, sensors=%d/type, interval=%ds",
        scenario["broker_mode"],
        scenario["sensor_count"],
        scenario["interval"],
    )
    _run_compose(["up", "-d", "--build"], env=env)


def _compose_stop_pc1() -> None:
    """Stop only pc1 (sensors + broker) between scenarios."""
    _run_compose(["stop", "pc1"], check=False)


def _compose_down() -> None:
    """Tear down all services and remove volumes."""
    _run_compose(["down", "-v"], check=False)


def _get_logs(service: str) -> str:
    """Get logs from a specific service."""
    return _run_compose(["logs", "--no-color", service], check=False)


# ---------------------------------------------------------------------------
# Measurement collection
# ---------------------------------------------------------------------------


def collect_throughput(start_iso: str, end_iso: str) -> int:
    """
    Query the replica DB on PC2 for the count of sensor events in the interval.

    Uses docker exec to run a Python snippet inside the pc2-analytics container
    that calls get_event_count_in_interval() on the replica database.
    """
    script = (
        "import sys; sys.path.insert(0, '/app'); "
        "from common.db_utils import TrafficDB; "
        f"db = TrafficDB('/data/traffic_replica.db'); "
        f"print(db.get_event_count_in_interval('{start_iso}', '{end_iso}'))"
    )
    result = subprocess.run(
        ["docker", "exec", "pc2-analytics", "python", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.warning("Throughput query failed: %s", result.stderr.strip())
        return -1
    try:
        return int(result.stdout.strip())
    except ValueError:
        logger.warning("Could not parse throughput: %s", result.stdout.strip())
        return -1


def collect_latency(service: str = "pc2") -> dict:
    """
    Parse [LATENCY] log lines from the semaphore control service.

    Returns dict with min, max, avg, p95, count, and raw samples (ms).
    """
    logs = _get_logs(service)
    samples = []
    for line in logs.splitlines():
        match = LATENCY_PATTERN.search(line)
        if match:
            samples.append(float(match.group(2)))

    if not samples:
        return {"min": 0, "max": 0, "avg": 0, "p95": 0, "count": 0, "samples": []}

    samples.sort()
    count = len(samples)
    p95_idx = int(count * 0.95)
    return {
        "min": round(min(samples), 2),
        "max": round(max(samples), 2),
        "avg": round(sum(samples) / count, 2),
        "p95": round(samples[min(p95_idx, count - 1)], 2),
        "count": count,
        "samples": [round(s, 2) for s in samples],
    }


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------


def run_scenario(scenario: dict, duration_sec: int) -> dict:
    """
    Run a single performance scenario and collect results.

    Args:
        scenario: Scenario definition dict (name, sensor_count, interval, broker_mode).
        duration_sec: How long to run the scenario in seconds.

    Returns:
        Results dict with throughput and latency data.
    """
    name = scenario["name"]
    logger.info("=" * 60)
    logger.info("SCENARIO %s", name)
    logger.info("=" * 60)

    # Record start time
    start_time = datetime.now(UTC)
    start_iso = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Start the system
    _compose_up(scenario)

    # Wait for the measurement window
    logger.info("Measurement window: %d seconds...", duration_sec)
    time.sleep(duration_sec)

    # Record end time
    end_time = datetime.now(UTC)
    end_iso = end_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Collect measurements
    logger.info("Collecting throughput...")
    throughput = collect_throughput(start_iso, end_iso)

    logger.info("Collecting latency...")
    latency = collect_latency("pc2")

    # Stop pc1 (keep pc2/pc3 for the next scenario to reuse DB)
    _compose_stop_pc1()

    result = {
        "scenario": name,
        "broker_mode": scenario["broker_mode"],
        "sensor_count": scenario["sensor_count"],
        "interval_sec": scenario["interval"],
        "duration_sec": duration_sec,
        "start_time": start_iso,
        "end_time": end_iso,
        "throughput_events": throughput,
        "latency_ms": {
            "min": latency["min"],
            "max": latency["max"],
            "avg": latency["avg"],
            "p95": latency["p95"],
            "count": latency["count"],
        },
    }

    logger.info(
        "Scenario %s results: throughput=%d events, latency avg=%.2f ms (%d samples)",
        name,
        throughput,
        latency["avg"],
        latency["count"],
    )

    return result


def run_all_scenarios(duration_sec: int, output_dir: str) -> list[dict]:
    """Run all 4 scenarios in sequence and save results."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    results = []

    try:
        for scenario in SCENARIOS:
            result = run_scenario(scenario, duration_sec)
            results.append(result)

            # Save intermediate results after each scenario
            results_file = output_path / "results.json"
            with open(results_file, "w") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            logger.info("Results saved to %s", results_file)

    except KeyboardInterrupt:
        logger.info("Interrupted by user. Saving partial results...")
    except Exception as e:
        logger.error("Error during scenario execution: %s", e)
    finally:
        # Tear down everything
        logger.info("Tearing down all services...")
        _compose_down()

        # Save final results
        if results:
            results_file = output_path / "results.json"
            with open(results_file, "w") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            logger.info("Final results saved to %s", results_file)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run performance test scenarios (Table 1 from project spec)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=120,
        help="Duration of each scenario in seconds (default: 120 = 2 minutes)",
    )
    parser.add_argument(
        "--output",
        default="benchmark_results",
        help="Output directory for results (default: benchmark_results)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    logger.info("Performance Test Runner")
    logger.info("Duration per scenario: %d seconds", args.duration)
    logger.info("Output directory: %s", args.output)
    logger.info("Scenarios to run: %d", len(SCENARIOS))

    results = run_all_scenarios(args.duration, args.output)

    # Print summary table
    if results:
        print("\n" + "=" * 70)
        print(f"{'Scenario':<10} {'Broker':<12} {'Sensors':<10} {'Throughput':<15} {'Latency avg'}")
        print("-" * 70)
        for r in results:
            print(
                f"{r['scenario']:<10} {r['broker_mode']:<12} "
                f"{r['sensor_count']}/type    "
                f"{r['throughput_events']:<15} "
                f"{r['latency_ms']['avg']:.2f} ms"
            )
        print("=" * 70)


if __name__ == "__main__":
    main()
