# Problem 4: Experiment output design (`experiment_report.csv`)

This document explains how **`analysis_outputs/experiment_report.csv`** is produced, what each column means, and how it relates to `export_instance_profits.py` and `analyze_generated_instances.py`, so you do not have to re-read the code later.

## Purpose (aligned with Assignment PDF Problem 4)

- **Heuristic**: `algorithm_module.heuristic_algorithm` builds a feasible solution for each instance (when format and feasibility checks pass), and reports **profit_heuristic** and its breakdown.
- **Benchmark (when instances are small enough)**: The same driver runs a **full Gurobi MIP** (isomorphic to `problem_1_code.py`) to obtain **mip_profit**; on success **`ub_type = OPT`**, and **gap_to_opt** is the relative gap between the heuristic and the MIP optimal solution.
- **Benchmark (when MIP yields no objective)**: e.g. license or size limits so **no mip_profit** — try **`LP_RELAX`** (same model as the MIP but variables in `[0,1]`; LP optimum \(\ge\) integer optimum for maximization, see `solve_lp_relaxation_gurobi`), using **`min(lp_obj, sum_k R_k)`** as `ub_profit`. If the LP also fails, fall back to **`ACCEPT_ALL_UB`** (`sum_k R_k`, see `profit_upper_bound_accept_all`).

> Older versions ran `problem_1_code.py` separately to write `instance_profits.csv`, which duplicated the analysis pipeline and could drift; output is now **unified** under `analyze_generated_instances.run_experiment`.

## How to generate files

| Command | Behavior |
|---------|----------|
| `python3 analyze_generated_instances.py` | Writes CSV (and `summary_by_scenario.csv`); PNGs only with `--with-plots` |
| `python3 export_instance_profits.py` | By default **CSV only** (faster), no plots |
| `python3 export_instance_profits.py --with-plots` | Same as the full analyze run (includes plots) |
| `python3 export_instance_profits.py --glob 'generated_instances_v2/*.txt'` | Custom glob for instance files |

Output files (default directory `analysis_outputs/`; override with `--output-dir` on `analyze_generated_instances.py` / `export_instance_profits.py`):

- **`experiment_report.csv`** — primary table (columns below); with `--tag X` the filename becomes `experiment_report_X.csv`.
- **`summary_by_scenario.csv`** — per-scenario aggregates (means / standard deviations); tagged as `summary_by_scenario_X.csv` when using `--tag`.

Archived tagged runs may live under `analysis_outputs/v1/`, `analysis_outputs/v2/upperbound1/`, etc.; see `analysis_outputs/README.txt`.

## Column definitions (one row per instance)

Column order is fixed by the constant `EXPERIMENT_REPORT_COLUMNS` for stable reporting and diffs.

| Column | Description |
|--------|-------------|
| `instance_path` | Path relative to the repo root (e.g. `generated_instances_v2/S1_baseline_01.txt`) |
| `scenario` | Scenario prefix inferred from the filename (e.g. `S1_baseline`) |
| `n_orders` | Number of orders `nK` |
| `feasible` | Whether the heuristic solution passes `evaluate` (time windows, level, dispatch, budget, etc.) |
| `profit_heuristic` | Objective when feasible; empty if infeasible |
| `revenue_heuristic` | When feasible: sum of revenue from accepted orders |
| `compensation_heuristic` | When feasible: sum of rejection compensation |
| `n_accepted_heuristic` | When feasible: number of accepted orders |
| `runtime_s` | *(Full columns only.)* Wall-clock seconds for that instance (heuristic + MIP attempt; LP relaxation when run for UB) |
| `gurobi_time/heuristic_time` | *(Minimal columns only.)* Two wall-clock parts as `gurobi_s/heuristic_s` (Gurobi/LP phase after the heuristic vs heuristic-only phase; six decimals each) |
| `mip_status` | Gurobi status string (`OPTIMAL`, `GUROBI_ERROR_10010`, etc.) |
| `mip_profit` | MIP objective; empty if no solution or error |
| `mip_mipgap` | MIP gap when an incumbent exists; otherwise empty |
| `mip_revenue` / `mip_compensation` / `mip_n_accepted` | Filled only when the MIP has an incumbent (assignment variables readable) |
| `gap_to_opt` | Only when **mip_profit** and **profit_heuristic** exist: `(mip_profit - profit_heuristic) / |mip_profit|` (denominator clamped to 1 to avoid division by zero) |
| `ub_type` | `OPT` (MIP optimum), `LP_RELAX` (LP relaxation upper bound), or `ACCEPT_ALL_UB` (sum of all order revenues) |
| `ub_profit` | Numeric value of the bound used |
| `gap_to_ub` | `(ub_profit - profit_heuristic) / |ub_profit|` when the heuristic is feasible and the bound exists |
| `error_heuristic` | Message when the heuristic is infeasible or raises |

## Maintenance notes

- If you change the MIP model, keep **`problem_1_code.py`**, **`analyze_generated_instances._build_car_rental_model`** / **`solve_optimal_gurobi`**, and **`solve_lp_relaxation_gurobi`** in sync so hand-in MIP, experiment MIP, and LP relaxation stay aligned.
- When adding columns: update `EXPERIMENT_REPORT_COLUMNS`, this document, and any tables in your written report.
