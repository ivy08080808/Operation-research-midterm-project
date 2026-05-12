#!/usr/bin/env python3
"""
Problem 4 experiment runner (internal script; not part of algorithm_module.py submission).

Outputs (default ``--output-dir analysis_outputs_small``):
  - problem4_instance_results.csv
  - problem4_summary_results.csv
  - problem4_gap_histogram.png
  - problem4_gap_histogram_by_scenario.png

Design:
  - Load or generate 10 instances per scenario (S1–S5) using ``generate_instances.py --scale small``.
  - For each instance:
      * run heuristic_algorithm() from algorithm_module.py
      * evaluate feasibility + heuristic profit
      * solve Gurobi benchmark (optimal MIP with a time limit)
      * compute gap = (benchmark_profit - heuristic_profit) / abs(benchmark_profit)
  - Summarize by scenario: feasibility rate, average profits, average gap, std gap, avg benchmark runtime.

Compatibility:
  - Script may use matplotlib; heuristic submission constraints do not apply here.
  - Intended to run under Python 3.12 (also works on 3.9+).
"""

from __future__ import annotations

import csv
import glob
import os
import statistics
import time
from dataclasses import dataclass
from typing import Optional

import matplotlib.pyplot as plt

import algorithm_module
import generate_instances
from analyze_generated_instances import evaluate, scenario_from_filename, solve_optimal_gurobi


SCENARIOS = (
    "S1_baseline",
    "S2_high_low_level_demand",
    "S3_geographic_imbalance",
    "S4_high_order_load",
    "S5_tight_relo_budget",
)


@dataclass(frozen=True)
class InstanceRow:
    scenario: str
    seed: int
    instance_path: str
    feasible: bool
    heuristic_profit: Optional[float]
    benchmark_profit: Optional[float]
    gap: Optional[float]
    heuristic_runtime_s: float
    benchmark_runtime_s: float
    benchmark_status: str


def _ensure_instances_small(n_per_scenario: int = 10) -> list[str]:
    """
    Ensure generated_instances_small/ contains >= n_per_scenario *.txt for each scenario.
    Returns the list of file paths to use (exactly len(SCENARIOS)*n_per_scenario).
    """
    out_dir = "generated_instances_small"
    ok = True
    for s in SCENARIOS:
        pat = os.path.join(out_dir, f"{s}_*.txt")
        if len(sorted(glob.glob(pat))) < n_per_scenario:
            ok = False
            break
    if not ok:
        generate_instances.main("small")

    chosen: list[str] = []
    for s in SCENARIOS:
        fps = sorted(glob.glob(os.path.join(out_dir, f"{s}_*.txt")))
        if len(fps) < n_per_scenario:
            raise SystemExit(f"Not enough instances for {s}: found {len(fps)}, need {n_per_scenario}")
        chosen.extend(fps[:n_per_scenario])
    return chosen


def _seed_from_filename(path: str) -> int:
    """
    Extract a stable seed identifier from a filename like S1_baseline_07.txt -> 7.
    Falls back to 0 if not present.
    """
    base = os.path.basename(path)
    stem = base.replace(".txt", "")
    parts = stem.split("_")
    # last token should be NN
    try:
        return int(parts[-1])
    except Exception:
        return 0


def _gap(bench: Optional[float], heur: Optional[float]) -> Optional[float]:
    if bench is None or heur is None:
        return None
    denom = abs(float(bench))
    if denom <= 1e-9:
        denom = 1.0
    return (float(bench) - float(heur)) / denom


def run(
    *,
    n_per_scenario: int = 10,
    mip_time_limit_s: float = 60.0,
    output_dir: str = "analysis_outputs_small",
) -> tuple[list[InstanceRow], str, str]:
    os.makedirs(output_dir, exist_ok=True)
    per_instance_csv = os.path.join(output_dir, "problem4_instance_results.csv")
    summary_csv = os.path.join(output_dir, "problem4_summary_results.csv")

    files = _ensure_instances_small(n_per_scenario=n_per_scenario)

    rows: list[InstanceRow] = []
    # Write per-instance CSV streaming (easy to copy + robust to long runs).
    with open(per_instance_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "scenario",
                "seed",
                "instance_path",
                "feasible",
                "heuristic_profit",
                "benchmark_profit",
                "gap",
                "heuristic_runtime_s",
                "benchmark_runtime_s",
                "benchmark_status",
            ],
        )
        w.writeheader()
        f.flush()

        for i, fp in enumerate(files, 1):
            scen = scenario_from_filename(fp)
            seed = _seed_from_filename(fp)

            # Heuristic
            t0 = time.time()
            try:
                assignment, relocation = algorithm_module.heuristic_algorithm(fp)
                feas, heur_profit, _info, _hstat = evaluate(fp, assignment, relocation)
            except Exception:
                feas, heur_profit = False, None
            ht = time.time() - t0

            # Benchmark (Gurobi)
            bt0 = time.time()
            try:
                mip = solve_optimal_gurobi(fp, time_limit_s=float(mip_time_limit_s))
                bench_profit = mip.obj
                bench_status = mip.status
            except Exception as e:
                bench_profit = None
                bench_status = f"ERROR_{type(e).__name__}"
            bt = time.time() - bt0

            gap = _gap(bench_profit, heur_profit)
            row = InstanceRow(
                scenario=scen,
                seed=int(seed),
                instance_path=fp,
                feasible=bool(feas),
                heuristic_profit=float(heur_profit) if heur_profit is not None else None,
                benchmark_profit=float(bench_profit) if bench_profit is not None else None,
                gap=float(gap) if gap is not None else None,
                heuristic_runtime_s=float(ht),
                benchmark_runtime_s=float(bt),
                benchmark_status=str(bench_status),
            )
            rows.append(row)
            w.writerow(row.__dict__)
            f.flush()

            print(
                f"[{i}/{len(files)}] {row.scenario} seed={row.seed} "
                f"feas={row.feasible} heur={row.heuristic_profit} "
                f"bench={row.benchmark_profit} gap={row.gap} "
                f"t_h={row.heuristic_runtime_s:.3f}s t_b={row.benchmark_runtime_s:.3f}s "
                f"bench_status={row.benchmark_status}",
                flush=True,
            )

    # Summary by scenario
    with open(summary_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "Scenario",
                "Feasibility Rate",
                "Average Heuristic Profit",
                "Average Benchmark Profit",
                "Average Gap",
                "Std Dev Gap",
                "Average Benchmark Runtime (s)",
            ],
        )
        w.writeheader()

        for s in SCENARIOS:
            rs = [r for r in rows if r.scenario.startswith(s)]
            if not rs:
                continue
            feas_rate = sum(1 for r in rs if r.feasible) / float(len(rs))
            heur_vals = [r.heuristic_profit for r in rs if r.heuristic_profit is not None]
            bench_vals = [r.benchmark_profit for r in rs if r.benchmark_profit is not None]
            gaps = [r.gap for r in rs if r.gap is not None]
            bench_times = [r.benchmark_runtime_s for r in rs if r.benchmark_runtime_s is not None]

            avg_heur = statistics.fmean(heur_vals) if heur_vals else None
            avg_bench = statistics.fmean(bench_vals) if bench_vals else None
            avg_gap = statistics.fmean(gaps) if gaps else None
            sd_gap = statistics.pstdev(gaps) if len(gaps) >= 2 else 0.0 if len(gaps) == 1 else None
            avg_bt = statistics.fmean(bench_times) if bench_times else None

            w.writerow(
                {
                    "Scenario": s,
                    "Feasibility Rate": round(feas_rate, 4),
                    "Average Heuristic Profit": round(avg_heur, 4) if avg_heur is not None else "",
                    "Average Benchmark Profit": round(avg_bench, 4) if avg_bench is not None else "",
                    "Average Gap": round(avg_gap, 6) if avg_gap is not None else "",
                    "Std Dev Gap": round(sd_gap, 6) if sd_gap is not None else "",
                    "Average Benchmark Runtime (s)": round(avg_bt, 4) if avg_bt is not None else "",
                }
            )
        f.flush()

    return rows, per_instance_csv, summary_csv


def plot_histograms(rows: list[InstanceRow], *, output_dir: str = "analysis_outputs_small") -> tuple[str, str]:
    os.makedirs(output_dir, exist_ok=True)
    out_all = os.path.join(output_dir, "problem4_gap_histogram.png")
    out_by = os.path.join(output_dir, "problem4_gap_histogram_by_scenario.png")

    gaps_all = [r.gap for r in rows if r.gap is not None]
    plt.figure(figsize=(8, 5))
    plt.hist(gaps_all, bins=15, edgecolor="black")
    plt.xlabel("Gap")
    plt.ylabel("Number of instances")
    plt.title("Distribution of Heuristic Optimality Gaps")
    plt.tight_layout()
    plt.savefig(out_all, dpi=200)
    plt.close()

    # By scenario: 1x5 subplots
    fig, axes = plt.subplots(1, 5, figsize=(18, 4), sharey=True)
    for ax, s in zip(axes, SCENARIOS):
        gs = [r.gap for r in rows if r.gap is not None and r.scenario.startswith(s)]
        ax.hist(gs, bins=10, edgecolor="black")
        ax.set_title(s)
        ax.set_xlabel("Gap")
    axes[0].set_ylabel("# instances")
    fig.suptitle("Heuristic Optimality Gaps by Scenario", y=1.02)
    fig.tight_layout()
    fig.savefig(out_by, dpi=200, bbox_inches="tight")
    plt.close(fig)

    return out_all, out_by


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Run Problem 4 experiments (S1–S5, 10 instances each).")
    ap.add_argument("--n-per-scenario", type=int, default=10)
    ap.add_argument("--mip-time-limit", type=float, default=60.0)
    ap.add_argument("--output-dir", default="analysis_outputs_small")
    args = ap.parse_args()

    rows, per_csv, sum_csv = run(
        n_per_scenario=int(args.n_per_scenario),
        mip_time_limit_s=float(args.mip_time_limit),
        output_dir=str(args.output_dir),
    )
    out_all, out_by = plot_histograms(rows, output_dir=str(args.output_dir))
    print(f"Wrote {per_csv}")
    print(f"Wrote {sum_csv}")
    print(f"Wrote {out_all}")
    print(f"Wrote {out_by}")


if __name__ == "__main__":
    main()

