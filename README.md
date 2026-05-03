# Operation Research Midterm Project (Car Rental Planning)

This repository contains code for the OR114-2 Spring 2026 midterm project.

## Files

- `OR114-2_midtermProject.pdf`: project specification.
- `instance01.txt` ~ `instance05.txt`: example instances (same format as the hidden test instances).
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

- **Spec & column definitions:** [docs/EXPERIMENT_REPORT.md](docs/EXPERIMENT_REPORT.md)
- **Full run** (heuristic + Gurobi MIP benchmark + plots): `python3 analyze_generated_instances.py`
- **Table only** (faster, no histogram PNGs): `python3 export_instance_profits.py`

Main output: **`analysis_outputs/experiment_report.csv`** (a copy is also written as `generated_instances_results.csv`).

