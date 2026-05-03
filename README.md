# Operation Research Midterm Project (Car Rental Planning)

This repository contains code for the OR114-2 Spring 2026 midterm project.

## Repository layout（給未來自己／隊友）

| 區塊 | 說明 |
|------|------|
| 根目錄 `.py` | 作業／評分用程式：`algorithm_module.py`（必交）、`problem_1_code.py`、`analyze_generated_instances.py`、`grading_feasibility_check.py` 等 |
| `docs/` | 實驗說明 [`EXPERIMENT_REPORT.md`](docs/EXPERIMENT_REPORT.md)；情境摘要 PDF：`scenario_summary.pdf` |
| `analysis_outputs/` | **可重新產生**：實驗 CSV（勿手改；跑腳本重算） |
| `generated_instances_v2.zip` | 隨機 instance 壓縮檔；解壓到 `generated_instances_v2/`（**不進版控**，見 `.gitignore`） |
| 名稱含 `exampleCode` / `OR114-2_midtermProject_example*` | 助教範例，與你正式提交的 `algorithm_module.py` 分開 |

本地若出現 `__MACOSX/`、`.local_backup_*/`：多為解 zip 或 git 工具留下，**可刪**；已在 `.gitignore` 忽略。

## Files

- `OR114-2_midtermProject.pdf`: project specification.
- `instance01.txt` ~ `instance05.txt`: example instances (若根目錄沒有，請從課程／助教管道取得；格式同 hidden tests)。
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

Main output: **`analysis_outputs/experiment_report.csv`**（另會寫入 `summary_by_scenario.csv`；跑 `--with-plots` 時才會出 histogram PNG）。

