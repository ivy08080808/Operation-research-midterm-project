# Problem 4：實驗輸出設計說明（`experiment_report.csv`）

本文件說明 **`analysis_outputs/experiment_report.csv`** 如何產生、欄位意義、以及與 `export_instance_profits.py` / `analyze_generated_instances.py` 的關係，避免之後重讀程式碼。

## 目的（對齊作業 PDF Problem 4）

- **Heuristic**：`algorithm_module.heuristic_algorithm` 對每個 instance 產生可行解（若格式／可行性檢查通過），並計算 **profit_heuristic** 與拆解。
- **Benchmark（小規模可用時）**：同一支程式內用 **Gurobi 完整 MIP**（與 `problem_1_code.py` 同構）求 **mip_profit**；成功時 **`ub_type = OPT`**，且 **gap_to_opt** 為 heuristic 與 MIP 最佳解的相對差距。
- **Benchmark（MIP 無法解時）**：例如授權／規模限制，則 **無 mip_profit**，改用 **`ACCEPT_ALL_UB`**（數學上合法的上界：`sum_k R_k`，見程式 `profit_upper_bound_accept_all`），並計算 **gap_to_ub**。這**不是** LP 鬆弛最佳解，而是作業 PDF 所說「大 instance 時可用上界／簡單法當基準」的一種實作。

> 舊版曾另跑 `problem_1_code.py` 子程序寫 `instance_profits.csv`，與分析腳本重複且易不一致；現已**統一**由 `analyze_generated_instances.run_experiment` 產出。

## 如何產生檔案

| 指令 | 行為 |
|------|------|
| `python3 analyze_generated_instances.py` | 寫入 CSV + **所有** PNG 圖（histogram 等） |
| `python3 export_instance_profits.py` | 預設**只寫 CSV**（較快），不畫圖 |
| `python3 export_instance_profits.py --with-plots` | 等同完整 analyze（含圖） |
| `python3 export_instance_profits.py --glob 'generated_instances_v2/*.txt'` | 指定 instance 檔案 glob |

輸出檔案：

- **`analysis_outputs/experiment_report.csv`** — 主表（欄位見下）。
- **`analysis_outputs/generated_instances_results.csv`** — 與主表**內容相同**的別名（給舊流程／Notebook 用）。
- **`analysis_outputs/summary_by_scenario.csv`** — 依 scenario 聚合的平均值／標準差。

## 欄位定義（單一 instance 一列）

欄位順序由程式常數 `EXPERIMENT_REPORT_COLUMNS` 固定，便於報告貼上與版本對照。

| 欄位 | 說明 |
|------|------|
| `instance_path` | 相對於專案根目錄的路徑（例如 `generated_instances_v2/S1_baseline_01.txt`） |
| `scenario` | 由檔名推得的 scenario 前綴（例如 `S1_baseline`） |
| `n_orders` | 訂單數 `nK` |
| `feasible` | Heuristic 解是否通過 `evaluate`（時間窗、等級、調度、預算等） |
| `profit_heuristic` | 可行時之目標值；不可行為空 |
| `revenue_heuristic` | 可行時：接受訂單營收之和 |
| `compensation_heuristic` | 可行時：拒單賠償之和 |
| `n_accepted_heuristic` | 可行時：接受訂單筆數 |
| `runtime_s` | 該 instance 整段流程（heuristic + MIP）之秒數 |
| `mip_status` | Gurobi 狀態字串（`OPTIMAL`、`GUROBI_ERROR_10010` 等） |
| `mip_profit` | MIP 目標值；無解或錯誤為空 |
| `mip_mipgap` | 若有 incumbent 可能有的 MIP gap；否則空 |
| `mip_revenue` / `mip_compensation` / `mip_n_accepted` | 僅在 MIP 有 incumbent（可讀出 x）時填入 |
| `gap_to_opt` | 僅当 **mip_profit** 存在且 **profit_heuristic** 存在：`(mip_profit - profit_heuristic) / |mip_profit|`（避免除零時分母用 1） |
| `ub_type` | `OPT`（用最佳解當上界）或 `ACCEPT_ALL_UB` |
| `ub_profit` | 所用上界之數值 |
| `gap_to_ub` | `(ub_profit - profit_heuristic) / |ub_profit|`（heuristic 可行且 ub 存在時） |
| `error_heuristic` | Heuristic 不可行或例外時的說明字串 |

## 維護注意

- 若修改 MIP 模型，請**同步** `problem_1_code.py` 與 `analyze_generated_instances.solve_optimal_gurobi`，避免「手交報告 MIP」與「實驗 MIP」不一致。
- 新增輸出欄位時：更新 `EXPERIMENT_REPORT_COLUMNS`、本文件、以及報告中的表格說明。
