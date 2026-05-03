analysis_outputs layout (regenerable; re-run scripts to refresh)

  v1/
    experiment_report.csv, summary_by_scenario.csv   — snapshot of a default (untagged) run
    experiment_report_v1.csv, summary_by_scenario_v1.csv

  v2/algo_version/
    experiment_report_v2.1.csv … v2.5.csv and matching summary_by_scenario_*.csv
    — tagged experiment exports focused on heuristic / pipeline iterations

  v2/upperbound_version/
    experiment_report_v2.5.1.csv, summary_by_scenario_v2.5.1.csv
      — LP relaxation + PuLP/CBC fallback when Gurobi MIP/LP is unavailable
    experiment_report_v2.5.2.csv, summary_by_scenario_v2.5.2.csv
      — same LP stack plus per-car chain caps (Q_c) in the model, CARD_UB min,
        and min(LP, CARD_UB, ACCEPT_ALL_UB); see module docstring in
        analyze_generated_instances.py

Write new outputs into a subfolder with:

  python3 export_instance_profits.py --tag v2.5.3 --output-dir analysis_outputs/v2/upperbound_version

Default when --output-dir is omitted: analysis_outputs/ (creates experiment_report.csv at that root).
