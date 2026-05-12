# Operation Research Midterm Project (Car Rental Planning)

This repository contains code for the OR114-2 Spring 2026 midterm project.

## Repository layout

| Area | Contents |
|------|----------|
| Root `*.py` | Assignment / grading scripts: `algorithm_module.py` (required submission), `problem_1_code.py`, `analyze_generated_instances.py`, `grading_feasibility_check.py`, etc. |
| `docs/` | Experiment notes [`EXPERIMENT_REPORT.md`](docs/EXPERIMENT_REPORT.md); scenario summary PDF: `scenario_summary.pdf` |
| `analysis_outputs_small/` | Problem 4 MIP benchmark CSV + histograms for **small** instances |
| `analysis_outputs_big/` | Problem 4-style evaluation (fast UB) CSV + histograms for **big** instances |
| `generated_instances_small/`, `generated_instances_big/` | 50 instances each (5 scenarios × 10); regenerate with `generate_instances.py` |
| Names containing `exampleCode` / `OR114-2_midtermProject_example*` | Course staff examples, separate from your submitted `algorithm_module.py` |

If you see `__MACOSX/` or `.local_backup_*/` locally, they often come from unzip or git tooling—**safe to delete**; they are listed in `.gitignore`.

## Files

- `OR114-2_midtermProject.pdf`: project specification.
- `instance01.txt` ~ `instance05.txt`: example instances (if missing in the repo root, obtain from the course; same format as hidden tests).
- `problem_1_code.py`: a Gurobi MIP model used for Problem 1 (optimal solve for small instance).
- `algorithm_module.py`: **Problem 2 submission file** (heuristic algorithm).

The following files are example codes provided by course staff (for understanding I/O formats):

- `OR114-2_midtermProject_exampleCode_algorithm_module.py`
- `OR114-2_midtermProject_exampleCode_grading_program.py`
- `OR114-2_midtermProject_exampleCode_MTP_lib.py`

## Problem 2: Heuristic (`algorithm_module.py`)

The course grader will call:

- `heuristic_algorithm(file_path)` in `algorithm_module.py`

Your function must return:

- **`assignment`**: a 1D integer list with length `n_K`
  - If order `i` is accepted: `assignment[i-1] = car_id`
  - If order `i` is rejected: `assignment[i-1] = -1`
- **`relocation`**: a 2D list of moves; each row is:
  - `[car_id(int), from_station(int), to_station(int), start_time_str("YYYY/MM/DD hh:mm")]`

## How to run locally

### Python

- Python **3.12**

### Quick sanity check (syntax)

```bash
python3 -m py_compile algorithm_module.py
```

### Running the staff example grading program (if you have it)

The provided example `grading_program.py` expects instance files under a `data/` folder.
If you want to run it locally, you can create `data/` and copy instances into it.

Example:

```bash
mkdir -p data
cp instance0*.txt data/
python3 OR114-2_midtermProject_exampleCode_grading_program.py
```

## Problem 4: Random instances & experiment table

- **Small (MIP benchmark):** `python3 run_problem4_experiments.py` (writes under `analysis_outputs_small/`).
- **Big (fast upper bound, no Gurobi):** `python3 run_problem4_big_fast_ub_experiments.py` (writes under `analysis_outputs_big/`).
- **Regenerate instances:** `python3 generate_instances.py --scale small` and `--scale big`.
- **Full experiment driver:** `python3 analyze_generated_instances.py` (defaults to small-instance glob).
- **Table only:** `python3 export_instance_profits.py`

Main CSV names in each bundle: **`problem4_instance_results.csv`**, **`problem4_summary_results.csv`** (histogram PNGs when produced).
