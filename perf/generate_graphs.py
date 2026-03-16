"""
generate_graphs.py - Generate performance comparison charts from scenario results.

Reads benchmark_results/results.json (output of run_scenarios.py) and produces
publication-quality bar charts comparing throughput and latency across the 4
experiment scenarios.

Charts generated:
    1. Throughput comparison (grouped by broker type)
    2. Latency comparison (grouped by broker type, with error bars)
    3. Throughput by scenario (all 4 scenarios)
    4. Latency by scenario (all 4 scenarios)

Usage:
    python -m perf.generate_graphs [--input benchmark_results/results.json]
                                   [--output benchmark_results/graphs]
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("GraphGen")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_results(results_path: str) -> list[dict]:
    """Load scenario results from JSON file."""
    path = Path(results_path)
    if not path.exists():
        logger.error("Results file not found: %s", path)
        sys.exit(1)

    with open(path) as f:
        data = json.load(f)

    logger.info("Loaded %d scenario results from %s", len(data), path)
    return data


# ---------------------------------------------------------------------------
# Chart generation
# ---------------------------------------------------------------------------


def plot_throughput_grouped(results: list[dict], output_dir: Path) -> None:
    """
    Grouped bar chart: throughput comparison.
    X-axis groups: sensor config (1/type, 2/type).
    Bars: standard vs threaded broker.
    Y-axis: events stored in 2-minute window.
    """
    # Organize data: {sensor_count: {broker_mode: throughput}}
    data: dict[int, dict[str, int]] = {}
    for r in results:
        sc = r["sensor_count"]
        bm = r["broker_mode"]
        if sc not in data:
            data[sc] = {}
        data[sc][bm] = r["throughput_events"]

    sensor_configs = sorted(data.keys())
    x = np.arange(len(sensor_configs))
    width = 0.35

    standard_vals = [data.get(sc, {}).get("standard", 0) for sc in sensor_configs]
    threaded_vals = [data.get(sc, {}).get("threaded", 0) for sc in sensor_configs]

    fig, ax = plt.subplots(figsize=(8, 5))
    bars1 = ax.bar(x - width / 2, standard_vals, width, label="Standard Broker", color="#4472C4")
    bars2 = ax.bar(x + width / 2, threaded_vals, width, label="Threaded Broker", color="#ED7D31")

    ax.set_xlabel("Sensors per Type", fontsize=12)
    ax.set_ylabel("Events Stored (2-minute window)", fontsize=12)
    ax.set_title("Throughput Comparison: Standard vs Threaded Broker", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{sc} sensor/type" for sc in sensor_configs])
    ax.legend()
    ax.bar_label(bars1, padding=3)
    ax.bar_label(bars2, padding=3)

    fig.tight_layout()
    filepath = output_dir / "throughput_grouped.png"
    fig.savefig(filepath, dpi=150)
    plt.close(fig)
    logger.info("Saved: %s", filepath)


def plot_latency_grouped(results: list[dict], output_dir: Path) -> None:
    """
    Grouped bar chart: latency comparison.
    X-axis groups: sensor config (1/type, 2/type).
    Bars: standard vs threaded broker.
    Y-axis: average latency (ms) with min/max error bars.
    """
    # Organize data: {sensor_count: {broker_mode: {avg, min, max}}}
    data: dict[int, dict[str, dict]] = {}
    for r in results:
        sc = r["sensor_count"]
        bm = r["broker_mode"]
        if sc not in data:
            data[sc] = {}
        data[sc][bm] = r["latency_ms"]

    sensor_configs = sorted(data.keys())
    x = np.arange(len(sensor_configs))
    width = 0.35

    def _get_latency(sc, mode):
        lat = data.get(sc, {}).get(mode, {"avg": 0, "min": 0, "max": 0})
        return lat["avg"], lat["min"], lat["max"]

    std_avgs, std_mins, std_maxs = [], [], []
    thr_avgs, thr_mins, thr_maxs = [], [], []
    for sc in sensor_configs:
        avg, mn, mx = _get_latency(sc, "standard")
        std_avgs.append(avg)
        std_mins.append(avg - mn)
        std_maxs.append(mx - avg)

        avg, mn, mx = _get_latency(sc, "threaded")
        thr_avgs.append(avg)
        thr_mins.append(avg - mn)
        thr_maxs.append(mx - avg)

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(
        x - width / 2,
        std_avgs,
        width,
        yerr=[std_mins, std_maxs],
        capsize=5,
        label="Standard Broker",
        color="#4472C4",
    )
    ax.bar(
        x + width / 2,
        thr_avgs,
        width,
        yerr=[thr_mins, thr_maxs],
        capsize=5,
        label="Threaded Broker",
        color="#ED7D31",
    )

    ax.set_xlabel("Sensors per Type", fontsize=12)
    ax.set_ylabel("Latency (ms)", fontsize=12)
    ax.set_title("Latency Comparison: Standard vs Threaded Broker", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{sc} sensor/type" for sc in sensor_configs])
    ax.legend()

    fig.tight_layout()
    filepath = output_dir / "latency_grouped.png"
    fig.savefig(filepath, dpi=150)
    plt.close(fig)
    logger.info("Saved: %s", filepath)


def plot_throughput_by_scenario(results: list[dict], output_dir: Path) -> None:
    """Bar chart: throughput for all 4 scenarios individually."""
    names = [r["scenario"] for r in results]
    values = [r["throughput_events"] for r in results]
    colors = ["#4472C4" if "standard" in r["broker_mode"] else "#ED7D31" for r in results]

    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(names, values, color=colors)

    ax.set_xlabel("Scenario", fontsize=12)
    ax.set_ylabel("Events Stored (2-minute window)", fontsize=12)
    ax.set_title("Throughput by Scenario", fontsize=14)
    ax.bar_label(bars, padding=3)

    # Add legend for colors
    from matplotlib.patches import Patch

    legend_elements = [
        Patch(facecolor="#4472C4", label="Standard Broker"),
        Patch(facecolor="#ED7D31", label="Threaded Broker"),
    ]
    ax.legend(handles=legend_elements)

    fig.tight_layout()
    filepath = output_dir / "throughput_by_scenario.png"
    fig.savefig(filepath, dpi=150)
    plt.close(fig)
    logger.info("Saved: %s", filepath)


def plot_latency_by_scenario(results: list[dict], output_dir: Path) -> None:
    """Bar chart: average latency for all 4 scenarios individually."""
    names = [r["scenario"] for r in results]
    avgs = [r["latency_ms"]["avg"] for r in results]
    mins = [r["latency_ms"]["avg"] - r["latency_ms"]["min"] for r in results]
    maxs = [r["latency_ms"]["max"] - r["latency_ms"]["avg"] for r in results]
    colors = ["#4472C4" if "standard" in r["broker_mode"] else "#ED7D31" for r in results]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(names, avgs, yerr=[mins, maxs], capsize=5, color=colors)

    ax.set_xlabel("Scenario", fontsize=12)
    ax.set_ylabel("Latency (ms)", fontsize=12)
    ax.set_title("Average Latency by Scenario (with min/max range)", fontsize=14)

    from matplotlib.patches import Patch

    legend_elements = [
        Patch(facecolor="#4472C4", label="Standard Broker"),
        Patch(facecolor="#ED7D31", label="Threaded Broker"),
    ]
    ax.legend(handles=legend_elements)

    fig.tight_layout()
    filepath = output_dir / "latency_by_scenario.png"
    fig.savefig(filepath, dpi=150)
    plt.close(fig)
    logger.info("Saved: %s", filepath)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate performance comparison graphs from scenario results",
    )
    parser.add_argument(
        "--input",
        default="benchmark_results/results.json",
        help="Path to results JSON file (default: benchmark_results/results.json)",
    )
    parser.add_argument(
        "--output",
        default="benchmark_results/graphs",
        help="Output directory for graph images (default: benchmark_results/graphs)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    results = load_results(args.input)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Generating graphs...")

    plot_throughput_grouped(results, output_dir)
    plot_latency_grouped(results, output_dir)
    plot_throughput_by_scenario(results, output_dir)
    plot_latency_by_scenario(results, output_dir)

    logger.info("All graphs saved to %s", output_dir)


if __name__ == "__main__":
    main()
