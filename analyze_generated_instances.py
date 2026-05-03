import glob
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple, Optional

import matplotlib.pyplot as plt
import pandas as pd

import algorithm_module
import gurobipy as gp
from gurobipy import GRB


DATE_FMT = "%Y/%m/%d %H:%M"
PLANNING_START = datetime(2023, 1, 1, 0, 0)

# Canonical CSV columns for Problem 4 report / README docs/EXPERIMENT_REPORT.md
EXPERIMENT_REPORT_COLUMNS = [
    "instance_path",
    "scenario",
    "n_orders",
    "feasible",
    "profit_heuristic",
    "revenue_heuristic",
    "compensation_heuristic",
    "n_accepted_heuristic",
    "runtime_s",
    "mip_status",
    "mip_profit",
    "mip_mipgap",
    "mip_revenue",
    "mip_compensation",
    "mip_n_accepted",
    "gap_to_opt",
    "ub_type",
    "ub_profit",
    "gap_to_ub",
    "error_heuristic",
]


class MIPResult(NamedTuple):
    status: str
    obj: Optional[float]
    mipgap: Optional[float]
    revenue: Optional[float]
    compensation: Optional[float]
    n_accepted: Optional[int]


def fmt_time(dt: datetime) -> str:
    return dt.strftime(DATE_FMT)


def parse_time(s: str) -> datetime:
    return datetime.strptime(s.strip(), DATE_FMT)


@dataclass(frozen=True)
class Order:
    req_level: int
    ps: int
    rs: int
    pt: datetime
    rt: datetime


def load_instance(path: str):
    raw = []
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln or "====" in ln:
                continue
            raw.append(ln)

    idx = 0
    idx += 1  # general header
    nS, nC, nL, nK, nD, B = map(int, raw[idx].split(","))
    idx += 1

    idx += 1  # car header
    cars = {}
    for _ in range(nC):
        cid, lvl, st = map(int, raw[idx].split(","))
        cars[cid] = (lvl, st)
        idx += 1

    idx += 1  # rate header
    rate = {}
    for _ in range(nL):
        lvl, r = map(int, raw[idx].split(","))
        rate[lvl] = r
        idx += 1

    idx += 1  # order header
    orders = {}
    for _ in range(nK):
        parts = raw[idx].split(",")
        oid = int(parts[0])
        orders[oid] = Order(
            req_level=int(parts[1]),
            ps=int(parts[2]),
            rs=int(parts[3]),
            pt=parse_time(parts[4]),
            rt=parse_time(parts[5]),
        )
        idx += 1

    idx += 1  # travel header
    T = {}
    for _ in range(nS * nS):
        i, j, tmin = map(int, raw[idx].split(","))
        T[(i, j)] = tmin
        idx += 1

    return nS, nC, nL, nK, nD, B, cars, rate, orders, T


def compute_R(orders, rate):
    R = {}
    for oid, o in orders.items():
        hrs = (o.rt - o.pt).total_seconds() / 3600.0
        R[oid] = rate[o.req_level] * hrs
    return R


def evaluate(path: str, assignment, relocation):
    nS, nC, nL, nK, nD, B, cars, rate, orders, T = load_instance(path)
    if not isinstance(assignment, list) or len(assignment) != nK:
        return False, None, "bad assignment length", None

    # group assigned orders by car
    per_car = {cid: [] for cid in cars}
    accepted = set()
    for oid in range(1, nK + 1):
        cid = assignment[oid - 1]
        if cid == -1:
            continue
        if cid not in cars:
            return False, None, f"unknown car {cid}", None
        per_car[cid].append(oid)
        accepted.add(oid)

    # relocations by car
    reloc_by_car = {cid: [] for cid in cars}
    for row in relocation:
        if not (isinstance(row, list) or isinstance(row, tuple)) or len(row) != 4:
            return False, None, "bad relocation row", None
        cid, fs, ts, st = row
        if cid not in cars:
            return False, None, f"unknown car in relocation {cid}", None
        try:
            st_dt = parse_time(st)
        except Exception:
            return False, None, "bad relocation time", None
        reloc_by_car[cid].append((st_dt, fs, ts))
    for cid in reloc_by_car:
        reloc_by_car[cid].sort(key=lambda x: x[0])

    total_move = 0

    for cid, oids in per_car.items():
        if not oids:
            continue
        oids.sort(key=lambda oid: (orders[oid].pt, oid))
        car_lvl, car_station = cars[cid]
        car_ready = PLANNING_START
        moves = reloc_by_car[cid]
        mi = 0

        def apply_moves_until(limit):
            nonlocal car_station, car_ready, mi, total_move
            while mi < len(moves) and moves[mi][0] < limit:
                start, fs, ts = moves[mi]
                if start < car_ready:
                    return False
                if fs != car_station:
                    return False
                dur = T[(fs, ts)]
                total_move += dur
                car_ready = start + timedelta(minutes=dur)
                car_station = ts
                mi += 1
            return True

        for oid in oids:
            o = orders[oid]
            if not (car_lvl == o.req_level or car_lvl == o.req_level + 1):
                return False, None, "level incompatible", None

            deadline = o.pt - timedelta(minutes=30)
            if deadline < PLANNING_START:
                deadline = PLANNING_START
            if not apply_moves_until(deadline):
                return False, None, "bad relocation chain", None
            if car_station != o.ps:
                return False, None, "not at pickup station", None
            if car_ready > deadline:
                return False, None, "not ready by deadline", None

            car_station = o.rs
            car_ready = o.rt + timedelta(hours=4)  # +1h late buffer +3h cleaning

        if not apply_moves_until(datetime.max):
            return False, None, "bad relocation after last order", None

    if total_move > B:
        return False, None, "relocation budget exceeded", None

    R = compute_R(orders, rate)
    revenue = sum(R[oid] for oid in accepted)
    rejected = set(orders.keys()) - accepted
    comp = sum(2 * R[oid] for oid in rejected)
    profit = revenue - comp
    stats = {
        "revenue_heuristic": float(revenue),
        "compensation_heuristic": float(comp),
        "n_accepted_heuristic": int(len(accepted)),
        "n_orders": int(nK),
    }
    return True, profit, f"moves={total_move}/{B}", stats


def profit_upper_bound_accept_all(path: str):
    """
    Simple valid upper bound on the optimal profit:
    - Let all orders be accepted (x_k <= 1), then the maximum possible revenue is sum_k R_k.
    - Since profit = sum(R_k x_k) - 2*sum(R_k (1-x_k)) = -2*sum R_k + 3*sum(R_k x_k),
      and sum(R_k x_k) <= sum R_k, we have profit <= sum R_k.
    Returns UB_profit.
    """
    _nS, _nC, _nL, _nK, _nD, _B, _cars, rate, orders, _T = load_instance(path)
    R = compute_R(orders, rate)
    return float(sum(R.values()))


def solve_optimal_gurobi(path: str, time_limit_s: float = 20.0) -> MIPResult:
    """
    Solve the instance with a MIP (Gurobi), adapted from problem_1_code.py.
    Returns MIPResult with objective, MIP gap when applicable, and revenue /
    compensation / accepted order count when an incumbent solution exists.
    """
    nS, nC, nL, nK, nD, B, cars, rate, orders, T = load_instance(path)

    K = list(orders.keys())
    C = list(cars.keys())

    R = compute_R(orders, rate)

    # feasible arcs between orders
    feasible_arcs = []
    for i in K:
        for j in K:
            if i == j:
                continue
            ready = orders[i].rt + timedelta(hours=4)  # +1h late +3h cleaning
            arrival = ready + timedelta(minutes=T[(orders[i].rs, orders[j].ps)])
            if arrival <= orders[j].pt - timedelta(minutes=30):
                feasible_arcs.append((i, j))

    arcs_in = {k: [] for k in K}
    arcs_out = {k: [] for k in K}
    for (i, j) in feasible_arcs:
        arcs_out[i].append(j)
        arcs_in[j].append(i)

    m = gp.Model("car_rental_opt")
    m.Params.OutputFlag = 0
    m.Params.TimeLimit = float(time_limit_s)

    x = m.addVars(K, vtype=GRB.BINARY, name="accept")
    assign = m.addVars(C, K, vtype=GRB.BINARY, name="assign")
    start = m.addVars(C, K, vtype=GRB.BINARY, name="start")
    end_v = m.addVars(C, K, vtype=GRB.BINARY, name="end")
    z = m.addVars(C, feasible_arcs, vtype=GRB.BINARY, name="z")

    # initial reachability
    for c in C:
        init_station = cars[c][1]
        for k in K:
            arrival = PLANNING_START + timedelta(minutes=T[(init_station, orders[k].ps)])
            deadline = orders[k].pt - timedelta(minutes=30)
            if deadline < PLANNING_START:
                deadline = PLANNING_START
            if arrival > deadline:
                m.addConstr(start[c, k] == 0)

    # accepted orders served by exactly one car
    for k in K:
        m.addConstr(gp.quicksum(assign[c, k] for c in C) == x[k])

    # level compatibility
    for c in C:
        cl = cars[c][0]
        for k in K:
            ol = orders[k].req_level
            if not (cl == ol or cl == ol + 1):
                m.addConstr(assign[c, k] == 0)

    # start/end imply assignment
    for c in C:
        for k in K:
            m.addConstr(start[c, k] <= assign[c, k])
            m.addConstr(end_v[c, k] <= assign[c, k])

    # at most one start/end per car
    for c in C:
        m.addConstr(gp.quicksum(start[c, k] for k in K) <= 1)
        m.addConstr(gp.quicksum(end_v[c, k] for k in K) <= 1)

    # flow conservation
    for c in C:
        for k in K:
            incoming = gp.quicksum(z[c, i, k] for i in arcs_in[k])
            outgoing = gp.quicksum(z[c, k, j] for j in arcs_out[k])
            m.addConstr(start[c, k] + incoming == assign[c, k])
            m.addConstr(end_v[c, k] + outgoing == assign[c, k])

    # chain balance
    for c in C:
        m.addConstr(
            gp.quicksum(start[c, k] for k in K) == gp.quicksum(end_v[c, k] for k in K)
        )

    # arc limits
    for c in C:
        for k in K:
            m.addConstr(gp.quicksum(z[c, i, k] for i in arcs_in[k]) <= 1)
            m.addConstr(gp.quicksum(z[c, k, j] for j in arcs_out[k]) <= 1)

    # relocation budget
    travel_between = gp.quicksum(
        T[(orders[i].rs, orders[j].ps)] * z[c, i, j] for c in C for (i, j) in feasible_arcs
    )
    initial_travel = gp.quicksum(
        T[(cars[c][1], orders[k].ps)] * start[c, k] for c in C for k in K
    )
    m.addConstr(travel_between + initial_travel <= int(B))

    # objective
    profit = gp.quicksum(R[k] * x[k] - 2 * R[k] * (1 - x[k]) for k in K)
    m.setObjective(profit, GRB.MAXIMIZE)

    m.optimize()

    def mip_financials() -> tuple:
        acc = [k for k in K if x[k].X > 0.5]
        rev = float(sum(R[k] for k in acc))
        rej = [k for k in K if k not in acc]
        comp_v = float(sum(2 * R[k] for k in rej))
        return rev, comp_v, int(len(acc))

    if m.Status == GRB.OPTIMAL:
        rev, comp_v, na = mip_financials()
        return MIPResult("OPTIMAL", float(m.ObjVal), 0.0, rev, comp_v, na)
    if m.Status == GRB.TIME_LIMIT:
        if m.SolCount > 0:
            rev, comp_v, na = mip_financials()
            return MIPResult(
                "TIME_LIMIT",
                float(m.ObjVal),
                float(m.MIPGap),
                rev,
                comp_v,
                na,
            )
        return MIPResult("TIME_LIMIT_NO_SOLUTION", None, None, None, None, None)
    if m.SolCount > 0:
        rev, comp_v, na = mip_financials()
        return MIPResult(f"STATUS_{m.Status}", float(m.ObjVal), float(m.MIPGap), rev, comp_v, na)
    return MIPResult(f"STATUS_{m.Status}", None, None, None, None, None)

def scenario_from_filename(fp: str) -> str:
    base = os.path.basename(fp)
    # e.g., S3_geographic_imbalance_07.txt -> S3_geographic_imbalance
    if base.count("_") >= 2:
        return "_".join(base.split("_")[:-1]).replace(".txt", "")
    return base.replace(".txt", "")


def _tagged_output_path(path: str, tag: Optional[str]) -> str:
    """Insert _{tag} before the extension, e.g. experiment_report_v2.1.csv"""
    if not tag:
        return path
    directory, fname = os.path.split(path)
    stem, ext = os.path.splitext(fname)
    tagged = f"{stem}_{tag}{ext}"
    return os.path.join(directory, tagged) if directory else tagged


def run_experiment(
    instance_glob: str = "generated_instances_v2/*.txt",
    *,
    write_plots: bool = True,
    mip_time_limit_s: float = 20.0,
    output_tag: Optional[str] = None,
) -> None:
    """
    Run heuristic + MIP benchmark on all matching instances; write unified CSVs under
    analysis_outputs/. See docs/EXPERIMENT_REPORT.md for column definitions.
    """
    files = sorted(glob.glob(instance_glob))
    if not files:
        raise SystemExit(
            f"No files matched {instance_glob!r}. Unzip generated_instances_v2.zip if needed."
        )

    os.makedirs("analysis_outputs", exist_ok=True)

    rows: list[dict] = []
    for fp in files:
        t0 = time.time()
        nK = load_instance(fp)[3]
        feas = False
        profit = None
        err = ""
        hstat: Optional[dict] = None
        gap_opt = None
        ub_type, ub_profit, gap_ub = "", None, None

        try:
            assignment, relocation = algorithm_module.heuristic_algorithm(fp)
            feas, profit, info, hstat = evaluate(fp, assignment, relocation)
            err = "" if feas else info
        except Exception as e:
            feas, profit, err, hstat = False, None, repr(e), None

        try:
            mip = solve_optimal_gurobi(fp, time_limit_s=mip_time_limit_s)
        except gp.GurobiError as ge:
            mip = MIPResult(f"GUROBI_ERROR_{ge.errno}", None, None, None, None, None)
        except Exception:
            mip = MIPResult("OPT_ERROR", None, None, None, None, None)

        opt_profit = mip.obj

        if opt_profit is not None:
            ub_type, ub_profit = "OPT", float(opt_profit)
        else:
            ub_type, ub_profit = "ACCEPT_ALL_UB", profit_upper_bound_accept_all(fp)

        if profit is not None and opt_profit is not None:
            denom2 = abs(opt_profit) if abs(opt_profit) > 1e-9 else 1.0
            gap_opt = (opt_profit - profit) / denom2
        else:
            gap_opt = None

        if profit is not None and ub_profit is not None:
            denom3 = abs(ub_profit) if abs(ub_profit) > 1e-9 else 1.0
            gap_ub = (ub_profit - profit) / denom3
        else:
            gap_ub = None

        dt = time.time() - t0
        rel_path = Path(fp).as_posix()

        rows.append(
            {
                "instance_path": rel_path,
                "scenario": scenario_from_filename(fp),
                "n_orders": int(nK),
                "feasible": bool(feas),
                "profit_heuristic": float(profit) if profit is not None else None,
                "revenue_heuristic": hstat["revenue_heuristic"] if hstat else None,
                "compensation_heuristic": hstat["compensation_heuristic"] if hstat else None,
                "n_accepted_heuristic": hstat["n_accepted_heuristic"] if hstat else None,
                "runtime_s": dt,
                "mip_status": mip.status,
                "mip_profit": float(mip.obj) if mip.obj is not None else None,
                "mip_mipgap": float(mip.mipgap) if mip.mipgap is not None else None,
                "mip_revenue": float(mip.revenue) if mip.revenue is not None else None,
                "mip_compensation": float(mip.compensation) if mip.compensation is not None else None,
                "mip_n_accepted": mip.n_accepted,
                "gap_to_opt": float(gap_opt) if gap_opt is not None else None,
                "ub_type": ub_type,
                "ub_profit": float(ub_profit) if ub_profit is not None else None,
                "gap_to_ub": float(gap_ub) if gap_ub is not None else None,
                "error_heuristic": err,
            }
        )

    df = pd.DataFrame(rows)
    out_main = df.reindex(columns=EXPERIMENT_REPORT_COLUMNS)
    out_experiment = _tagged_output_path("analysis_outputs/experiment_report.csv", output_tag)
    out_main.to_csv(out_experiment, index=False)

    # Summary table
    summary = (
        df.groupby("scenario")
        .agg(
            n=("instance_path", "count"),
            feasible_rate=("feasible", "mean"),
            profit_heuristic_mean=("profit_heuristic", "mean"),
            profit_heuristic_std=("profit_heuristic", "std"),
            mip_profit_mean=("mip_profit", "mean"),
            mip_profit_std=("mip_profit", "std"),
            gap_to_opt_mean=("gap_to_opt", "mean"),
            gap_to_opt_std=("gap_to_opt", "std"),
            ub_profit_mean=("ub_profit", "mean"),
            ub_profit_std=("ub_profit", "std"),
            gap_to_ub_mean=("gap_to_ub", "mean"),
            gap_to_ub_std=("gap_to_ub", "std"),
            runtime_mean=("runtime_s", "mean"),
        )
        .reset_index()
        .sort_values("scenario")
    )
    out_summary = _tagged_output_path("analysis_outputs/summary_by_scenario.csv", output_tag)
    summary.to_csv(out_summary, index=False)

    if not write_plots:
        print(f"Wrote {out_experiment} and {out_summary}")
        print(summary)
        return

    # Plot: profit histograms per scenario (feasible only)
    scenarios = sorted(df["scenario"].unique())
    for sc in scenarios:
        sub = df[
            (df["scenario"] == sc)
            & (df["feasible"] == True)
            & (df["profit_heuristic"].notna())
        ]
        if sub.empty:
            continue
        plt.figure(figsize=(7, 4))
        plt.hist(sub["profit_heuristic"], bins=10)
        plt.title(f"Profit histogram — {sc} (feasible only)")
        plt.xlabel("Profit (heuristic)")
        plt.ylabel("Count")
        plt.tight_layout()
        plt.savefig(
            _tagged_output_path(f"analysis_outputs/profit_hist_{sc}.png", output_tag),
            dpi=200,
        )
        plt.close()

    # Plot: boxplot across scenarios
    box_data = []
    labels = []
    for sc in scenarios:
        sub = df[
            (df["scenario"] == sc)
            & (df["feasible"] == True)
            & (df["profit_heuristic"].notna())
        ]
        if sub.empty:
            continue
        box_data.append(sub["profit_heuristic"].values)
        labels.append(sc)
    if box_data:
        plt.figure(figsize=(10, 5))
        plt.boxplot(box_data, labels=labels, showfliers=False)
        plt.xticks(rotation=25, ha="right")
        plt.title("Profit by scenario (feasible only)")
        plt.ylabel("Profit (heuristic)")
        plt.tight_layout()
        plt.savefig(
            _tagged_output_path("analysis_outputs/profit_boxplot_by_scenario.png", output_tag),
            dpi=200,
        )
        plt.close()

    # Plot: feasibility rate bar
    plt.figure(figsize=(10, 4))
    plt.bar(summary["scenario"], summary["feasible_rate"])
    plt.xticks(rotation=25, ha="right")
    plt.ylim(0, 1.0)
    plt.title("Feasible rate by scenario")
    plt.ylabel("Feasible rate")
    plt.tight_layout()
    plt.savefig(
        _tagged_output_path("analysis_outputs/feasible_rate_by_scenario.png", output_tag),
        dpi=200,
    )
    plt.close()

    # Plot: gap-to-opt histogram per scenario (where mip solved)
    for sc in scenarios:
        sub = df[
            (df["scenario"] == sc)
            & (df["feasible"] == True)
            & (df["gap_to_opt"].notna())
        ]
        if sub.empty:
            continue
        plt.figure(figsize=(7, 4))
        plt.hist(sub["gap_to_opt"], bins=10)
        plt.title(f"Gap to optimal — {sc} (where solved)")
        plt.xlabel("(mip_profit - profit_heuristic) / |mip_profit|")
        plt.ylabel("Count")
        plt.tight_layout()
        plt.savefig(
            _tagged_output_path(f"analysis_outputs/gap_to_opt_hist_{sc}.png", output_tag),
            dpi=200,
        )
        plt.close()

    # Plot: gap-to-UB histogram per scenario
    for sc in scenarios:
        sub = df[
            (df["scenario"] == sc)
            & (df["feasible"] == True)
            & (df["gap_to_ub"].notna())
        ]
        if sub.empty:
            continue
        plt.figure(figsize=(7, 4))
        plt.hist(sub["gap_to_ub"], bins=10)
        plt.title(f"Gap to UB — {sc} (feasible only)")
        plt.xlabel("(ub_profit - profit_heuristic) / |ub_profit|")
        plt.ylabel("Count")
        plt.tight_layout()
        plt.savefig(
            _tagged_output_path(f"analysis_outputs/gap_to_ub_hist_{sc}.png", output_tag),
            dpi=200,
        )
        plt.close()

    print(f"Wrote tagged outputs under analysis_outputs/ (tag={output_tag!r})")
    print(summary)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Run full experiment (heuristic + MIP) and write analysis_outputs/.")
    ap.add_argument(
        "--glob",
        default="generated_instances_v2/*.txt",
        help="Instance file glob",
    )
    ap.add_argument(
        "--no-plots",
        action="store_true",
        help="Skip PNG figures (CSV only)",
    )
    ap.add_argument(
        "--mip-time-limit",
        type=float,
        default=20.0,
        help="Gurobi time limit per instance (seconds)",
    )
    ap.add_argument(
        "--tag",
        default=None,
        help="Suffix for output files, e.g. v2.1",
    )
    args = ap.parse_args()
    run_experiment(
        instance_glob=args.glob,
        write_plots=not args.no_plots,
        mip_time_limit_s=args.mip_time_limit,
        output_tag=args.tag,
    )


if __name__ == "__main__":
    main()

