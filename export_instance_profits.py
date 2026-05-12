#!/usr/bin/env python3
"""
Thin wrapper: runs the same experiment pipeline as analyze_generated_instances.

Writes the unified table to analysis_outputs_small/experiment_report.csv (same pipeline
as analyze_generated_instances). No separate MIP-via-problem_1_code path.

By default this skips figure generation (faster). Pass --with-plots to also write histograms.
"""

import argparse
import sys

from analyze_generated_instances import run_experiment


def main() -> int:
    p = argparse.ArgumentParser(description="Export unified experiment CSV to analysis_outputs_small/")
    p.add_argument(
        "--with-plots",
        action="store_true",
        help="Also write PNG plots (same as full analyze_generated_instances.py)",
    )
    p.add_argument(
        "--glob",
        default="generated_instances_small/*.txt",
        help="Instance file glob (default: generated_instances_small/*.txt)",
    )
    p.add_argument(
        "--tag",
        default=None,
        help="Output filename tag, e.g. v2.1 -> experiment_report_v2.1.csv",
    )
    p.add_argument(
        "--mip-time-limit",
        type=float,
        default=20.0,
        help="Gurobi MIP time limit per instance (seconds)",
    )
    p.add_argument(
        "--lp-time-limit",
        type=float,
        default=45.0,
        help="LP relaxation time limit when MIP has no objective (seconds)",
    )
    p.add_argument(
        "--output-dir",
        default="analysis_outputs_small",
        help="Directory for CSV outputs (default: analysis_outputs_small)",
    )
    p.add_argument(
        "--no-stream-csv",
        action="store_true",
        help="Write experiment_report only after all instances finish",
    )
    p.add_argument(
        "--high-priority",
        action="store_true",
        help="Best-effort higher CPU priority (may require admin)",
    )
    args = p.parse_args()
    run_experiment(
        instance_glob=args.glob,
        write_plots=args.with_plots,
        mip_time_limit_s=args.mip_time_limit,
        lp_time_limit_s=args.lp_time_limit,
        output_tag=args.tag,
        output_dir=args.output_dir,
        stream_csv=not args.no_stream_csv,
        high_priority=args.high_priority,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
