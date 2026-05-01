import glob
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import matplotlib.pyplot as plt
import pandas as pd

import algorithm_module
import gurobipy as gp
from gurobipy import GRB


DATE_FMT = "%Y/%m/%d %H:%M"
PLANNING_START = datetime(2023, 1, 1, 0, 0)


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
        return False, None, "bad assignment length"

    # group assigned orders by car
    per_car = {cid: [] for cid in cars}
    accepted = set()
    for oid in range(1, nK + 1):
        cid = assignment[oid - 1]
        if cid == -1:
            continue
        if cid not in cars:
            return False, None, f"unknown car {cid}"
        per_car[cid].append(oid)
        accepted.add(oid)

    # relocations by car
    reloc_by_car = {cid: [] for cid in cars}
    for row in relocation:
        if not (isinstance(row, list) or isinstance(row, tuple)) or len(row) != 4:
            return False, None, "bad relocation row"
        cid, fs, ts, st = row
        if cid not in cars:
            return False, None, f"unknown car in relocation {cid}"
        try:
            st_dt = parse_time(st)
        except Exception:
            return False, None, "bad relocation time"
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
                return False, None, "level incompatible"

            deadline = o.pt - timedelta(minutes=30)
            if deadline < PLANNING_START:
                deadline = PLANNING_START
            if not apply_moves_until(deadline):
                return False, None, "bad relocation chain"
            if car_station != o.ps:
                return False, None, "not at pickup station"
            if car_ready > deadline:
                return False, None, "not ready by deadline"

            car_station = o.rs
            car_ready = o.rt + timedelta(hours=4)  # +1h late buffer +3h cleaning

        if not apply_moves_until(datetime.max):
            return False, None, "bad relocation after last order"

    if total_move > B:
        return False, None, "relocation budget exceeded"

    R = compute_R(orders, rate)
    revenue = sum(R[oid] for oid in accepted)
    rejected = set(orders.keys()) - accepted
    comp = sum(2 * R[oid] for oid in rejected)
    profit = revenue - comp
    return True, profit, f"moves={total_move}/{B}"


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


def solve_optimal_gurobi(path: str, time_limit_s: float = 20.0):
    """
    Solve the instance with a MIP (Gurobi), adapted from problem_1_code.py.
    Returns: (status_str, obj_val_or_None, mip_gap_or_None)
    - OPTIMAL: exact optimum
    - TIME_LIMIT: best incumbent objective if available + MIPGap
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

    if m.Status == GRB.OPTIMAL:
        return "OPTIMAL", float(m.ObjVal), 0.0
    if m.Status == GRB.TIME_LIMIT:
        if m.SolCount > 0:
            return "TIME_LIMIT", float(m.ObjVal), float(m.MIPGap)
        return "TIME_LIMIT_NO_SOLUTION", None, None
    if m.SolCount > 0:
        return f"STATUS_{m.Status}", float(m.ObjVal), float(m.MIPGap)
    return f"STATUS_{m.Status}", None, None

def scenario_from_filename(fp: str) -> str:
    base = os.path.basename(fp)
    # e.g., S3_geographic_imbalance_07.txt -> S3_geographic_imbalance
    if base.count("_") >= 2:
        return "_".join(base.split("_")[:-1]).replace(".txt", "")
    return base.replace(".txt", "")


def main():
    files = sorted(glob.glob("generated_instances/*.txt"))
    if not files:
        raise SystemExit("No generated_instances/*.txt found. Unzip generated_instances.zip first.")

    os.makedirs("analysis_outputs", exist_ok=True)

    rows = []
    for fp in files:
        t0 = time.time()
        feas = False
        profit = None
        err = ""
        opt_status, opt_profit, opt_mipgap = "NOT_RUN", None, None
        gap_opt = None
        ub_type, ub_profit, gap_ub = "", None, None

        try:
            # main heuristic
            assignment, relocation = algorithm_module.heuristic_algorithm(fp)
            feas, profit, info = evaluate(fp, assignment, relocation)

            err = "" if feas else info
        except Exception as e:
            # If the heuristic itself crashes, we cannot compute anything meaningful.
            feas, profit, err = False, None, repr(e)

        # Step 2: try to get optimal by Gurobi (only depends on instance, not on heuristic feasibility)
        try:
            opt_status, opt_profit, opt_mipgap = solve_optimal_gurobi(fp, time_limit_s=20.0)
        except gp.GurobiError as ge:
            opt_status, opt_profit, opt_mipgap = f"GUROBI_ERROR_{ge.errno}", None, None
        except Exception as e:
            opt_status, opt_profit, opt_mipgap = "OPT_ERROR", None, None

        # Step 3: if optimal not available, compute an upper bound to still report a gap
        # (use a simple valid UB: profit <= sum_k R_k)
        if opt_profit is not None:
            ub_type, ub_profit = "OPT", float(opt_profit)
        else:
            ub_type, ub_profit = "ACCEPT_ALL_UB", profit_upper_bound_accept_all(fp)

        # gaps
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

        rows.append(
            {
                "file": fp,
                "scenario": scenario_from_filename(fp),
                "time_s": dt,
                "feasible": bool(feas),
                "profit": float(profit) if profit is not None else None,
                "opt_status": opt_status,
                "opt_profit": float(opt_profit) if opt_profit is not None else None,
                "opt_mipgap": float(opt_mipgap) if opt_mipgap is not None else None,
                "gap_to_opt": float(gap_opt) if gap_opt is not None else None,
                "ub_type": ub_type,
                "ub_profit": float(ub_profit) if ub_profit is not None else None,
                "gap_to_ub": float(gap_ub) if gap_ub is not None else None,
                "error": err,
            }
        )

    df = pd.DataFrame(rows)
    df.to_csv("analysis_outputs/generated_instances_results.csv", index=False)

    # Summary table
    summary = (
        df.groupby("scenario")
        .agg(
            n=("file", "count"),
            feasible_rate=("feasible", "mean"),
            profit_mean=("profit", "mean"),
            profit_std=("profit", "std"),
            opt_profit_mean=("opt_profit", "mean"),
            opt_profit_std=("opt_profit", "std"),
            gap_to_opt_mean=("gap_to_opt", "mean"),
            gap_to_opt_std=("gap_to_opt", "std"),
            ub_profit_mean=("ub_profit", "mean"),
            ub_profit_std=("ub_profit", "std"),
            gap_to_ub_mean=("gap_to_ub", "mean"),
            gap_to_ub_std=("gap_to_ub", "std"),
            time_mean=("time_s", "mean"),
        )
        .reset_index()
        .sort_values("scenario")
    )
    summary.to_csv("analysis_outputs/summary_by_scenario.csv", index=False)

    # Plot: profit histograms per scenario (feasible only)
    scenarios = sorted(df["scenario"].unique())
    for sc in scenarios:
        sub = df[(df["scenario"] == sc) & (df["feasible"] == True) & (df["profit"].notna())]
        if sub.empty:
            continue
        plt.figure(figsize=(7, 4))
        plt.hist(sub["profit"], bins=10)
        plt.title(f"Profit histogram — {sc} (feasible only)")
        plt.xlabel("Profit")
        plt.ylabel("Count")
        plt.tight_layout()
        plt.savefig(f"analysis_outputs/profit_hist_{sc}.png", dpi=200)
        plt.close()

    # Plot: boxplot across scenarios
    box_data = []
    labels = []
    for sc in scenarios:
        sub = df[(df["scenario"] == sc) & (df["feasible"] == True) & (df["profit"].notna())]
        if sub.empty:
            continue
        box_data.append(sub["profit"].values)
        labels.append(sc)
    if box_data:
        plt.figure(figsize=(10, 5))
        plt.boxplot(box_data, labels=labels, showfliers=False)
        plt.xticks(rotation=25, ha="right")
        plt.title("Profit by scenario (feasible only)")
        plt.ylabel("Profit")
        plt.tight_layout()
        plt.savefig("analysis_outputs/profit_boxplot_by_scenario.png", dpi=200)
        plt.close()

    # Plot: feasibility rate bar
    plt.figure(figsize=(10, 4))
    plt.bar(summary["scenario"], summary["feasible_rate"])
    plt.xticks(rotation=25, ha="right")
    plt.ylim(0, 1.0)
    plt.title("Feasible rate by scenario")
    plt.ylabel("Feasible rate")
    plt.tight_layout()
    plt.savefig("analysis_outputs/feasible_rate_by_scenario.png", dpi=200)
    plt.close()

    # Plot: gap-to-opt histogram per scenario (where opt_profit exists)
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
        plt.xlabel("(opt_profit - profit) / |opt_profit|")
        plt.ylabel("Count")
        plt.tight_layout()
        plt.savefig(f"analysis_outputs/gap_to_opt_hist_{sc}.png", dpi=200)
        plt.close()

    # Plot: gap-to-UB histogram per scenario (always available if profit computed)
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
        plt.xlabel("(ub_profit - profit) / |ub_profit|")
        plt.ylabel("Count")
        plt.tight_layout()
        plt.savefig(f"analysis_outputs/gap_to_ub_hist_{sc}.png", dpi=200)
        plt.close()

    print("Wrote outputs to analysis_outputs/")
    print(summary)


if __name__ == "__main__":
    main()

