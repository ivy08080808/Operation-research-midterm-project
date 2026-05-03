#!/usr/bin/env python3
"""
Thin wrapper: runs the same experiment pipeline as analyze_generated_instances.

Writes the unified table to analysis_outputs/experiment_report.csv (same pipeline
as analyze_generated_instances). No separate MIP-via-problem_1_code path.

By default this skips figure generation (faster). Pass --with-plots to also write histograms.
"""

import argparse
import sys

from analyze_generated_instances import run_experiment


def main() -> int:
    p = argparse.ArgumentParser(description="Export unified experiment CSV to analysis_outputs/")
    p.add_argument(
        "--with-plots",
        action="store_true",
        help="Also write PNG plots (same as full analyze_generated_instances.py)",
    )
    p.add_argument(
        "--glob",
        default="generated_instances_v2/*.txt",
        help="Instance file glob (default: generated_instances_v2/*.txt)",
    )
    args = p.parse_args()
    run_experiment(instance_glob=args.glob, write_plots=args.with_plots)
    return 0


if __name__ == "__main__":
    sys.exit(main())
