"""
Experiment driver for Problem 4: heuristic benchmark, optional Gurobi MIP, and
reported upper bounds on optimal profit (``ub_profit`` / ``ub_type``).

How the upper bound is tightened (reporting pipeline)
------------------------------------------------------
1. **OPT** — If the Gurobi MIP returns a finite objective, ``ub_profit`` is that
   optimum (``ub_type = OPT``). This is the tightest reference when the license
   allows solving the full integer model.

2. **LP relaxation** — If the MIP gives no objective, we relax every binary
   variable to ``[0, 1]`` on the *same* routing / assignment structure. The LP
   optimum is a valid upper bound on the integer maximum. We try Gurobi first
   (``LP_RELAX``); if ``optimize()`` fails (e.g. size-limited license), we solve
   the same LP with **PuLP + CBC** (``LP_RELAX_CBC``) so the bound does not
   collapse to the weak ``ACCEPT_ALL_UB`` alone.

3. **Per-car order caps (valid inequalities)** — For each car ``c``, we compute
   ``Q_c``, the maximum number of orders that car can serve in a single feasible
   chain under the instance relocation budget ``B`` (DP over ``feasible_arcs``
   with the same travel accounting as the MIP). We add
   ``sum_k assign[c, k] <= Q_c`` to both the Gurobi LP and the PuLP model. This
   cuts fractional solutions that ``split`` infeasible workload across cars and
   tightens the LP polyhedron without removing any integer-feasible solution.

4. **Cardinality combinatorial bound** — With
   ``M = min(|K|, sum_c Q_c)``, no feasible solution accepts more than ``M``
   orders, so total revenue ``sum_k R_k x_k`` is at most the sum of the ``M``
   largest ``R_k``. Mapping through
   ``profit = -2 * sum_k R_k + 3 * sum_k R_k x_k`` yields ``CARD_UB``.

5. **Final reported bound** — When the MIP is unavailable, we set
   ``ub_profit = min(LP objective, CARD_UB, ACCEPT_ALL_UB)`` where
   ``ACCEPT_ALL_UB = sum_k R_k`` (weak but always valid). ``ub_type`` records
   which term achieved the minimum (ties prefer the LP label when applicable).

See also: ``docs/EXPERIMENT_REPORT.md``, ``export_instance_profits.py``,
``analysis_outputs/README.txt``.
"""

import glob
import os
import time
from functools import lru_cache
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


def _car_can_reach_order_first(car_id: int, k: int, orders, cars, T) -> bool:
    init_station = cars[car_id][1]
    arrival = PLANNING_START + timedelta(minutes=T[(init_station, orders[k].ps)])
    deadline = orders[k].pt - timedelta(minutes=30)
    if deadline < PLANNING_START:
        deadline = PLANNING_START
    return arrival <= deadline


def _max_orders_chain_single_car(
    car_id: int, orders, cars, T, feasible_arcs, B: int
) -> int:
    """
    Maximum orders one car can serve along feasible_arcs with total modeled relocation <= B
    (same travel accounting as the MIP). Valid upper bound on sum_k assign[car_id, k].
    """
    K = list(orders.keys())
    init = cars[car_id][1]
    neg_inf = -10**9
    dp = {k: [neg_inf] * (B + 1) for k in K}
    for k in K:
        if not _car_can_reach_order_first(car_id, k, orders, cars, T):
            continue
        cl, _ = cars[car_id]
        if not (cl == orders[k].req_level or cl == orders[k].req_level + 1):
            continue
        t0 = int(T[(init, orders[k].ps)])
        if t0 <= B:
            dp[k][t0] = max(dp[k][t0], 1)

    clv, _ = cars[car_id]
    for _repeat in range(len(K) + 2):
        for (i, j) in feasible_arcs:
            olj = orders[j].req_level
            if not (clv == olj or clv == olj + 1):
                continue
            w = int(T[(orders[i].rs, orders[j].ps)])
            dpi = dp[i]
            dpj = dp[j]
            for t in range(B - w + 1):
                if dpi[t] <= neg_inf // 2:
                    continue
                nt = t + w
                cand = dpi[t] + 1
                if cand > dpj[nt]:
                    dpj[nt] = cand

    best = 0
    for k in K:
        for t in range(B + 1):
            if dp[k][t] > best:
                best = dp[k][t]
    return int(best)


@lru_cache(maxsize=256)
def compute_per_car_order_caps(path: str) -> dict[int, int]:
    """Q_c: valid upper bounds for inequalities sum_k assign[c,k] <= Q_c."""
    _K, C, _R, feasible_arcs, _ai, _ao, orders, cars, T, B = _car_rental_instance_data(path)
    Bi = int(B)
    return {c: _max_orders_chain_single_car(c, orders, cars, T, feasible_arcs, Bi) for c in C}


def profit_upper_bound_cardinality(path: str) -> float:
    """
    profit = -2*sum R + 3*sum_k R_k x_k. Using sum_k x_k <= min(|K|, sum_c Q_c),
    bound revenue by the sum of the largest M values of R_k.
    """
    _nS, _nC, _nL, nK, _nD, _B, _cars, rate, orders, _T = load_instance(path)
    Rdict = compute_R(orders, rate)
    S = float(sum(Rdict.values()))
    vals = sorted(Rdict.values(), reverse=True)
    caps = compute_per_car_order_caps(path)
    M = min(int(nK), int(sum(caps.values())))
    M = max(0, M)
    rev_ub = float(sum(vals[:M]))
    return -2.0 * S + 3.0 * rev_ub


def _car_rental_instance_data(path: str):
    """
    Shared topology for Gurobi MIP/LP and PuLP LP relaxation (same as problem_1_code.py).
    Returns K, C, R, feasible_arcs, arcs_in, arcs_out, orders, cars, T, B.
    """
    _nS, _nC, _nL, _nK, _nD, B, cars, rate, orders, T = load_instance(path)

    K = list(orders.keys())
    C = list(cars.keys())

    R = compute_R(orders, rate)

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

    return K, C, R, feasible_arcs, arcs_in, arcs_out, orders, cars, T, B


def _build_car_rental_model(path: str, *, continuous_relaxation: bool):
    """
    Same structure as problem_1_code.py / solve_optimal_gurobi.
    If continuous_relaxation=True, all former binary vars are in [0,1] (LP relaxation).
    Returns (model, K, C, R, x, assign, start, end_v, z, orders, cars, T, B).
    """
    K, C, R, feasible_arcs, arcs_in, arcs_out, orders, cars, T, B = _car_rental_instance_data(path)

    name = "car_rental_lp_relax" if continuous_relaxation else "car_rental_opt"
    m = gp.Model(name)
    m.Params.OutputFlag = 0

    if continuous_relaxation:
        vt = GRB.CONTINUOUS
    else:
        vt = GRB.BINARY

    x = m.addVars(K, vtype=vt, lb=0.0, ub=1.0, name="accept")
    assign = m.addVars(C, K, vtype=vt, lb=0.0, ub=1.0, name="assign")
    start = m.addVars(C, K, vtype=vt, lb=0.0, ub=1.0, name="start")
    end_v = m.addVars(C, K, vtype=vt, lb=0.0, ub=1.0, name="end")
    z = m.addVars(C, feasible_arcs, vtype=vt, lb=0.0, ub=1.0, name="z")

    for c in C:
        init_station = cars[c][1]
        for k in K:
            arrival = PLANNING_START + timedelta(minutes=T[(init_station, orders[k].ps)])
            deadline = orders[k].pt - timedelta(minutes=30)
            if deadline < PLANNING_START:
                deadline = PLANNING_START
            if arrival > deadline:
                m.addConstr(start[c, k] == 0)

    for k in K:
        m.addConstr(gp.quicksum(assign[c, k] for c in C) == x[k])

    for c in C:
        cl = cars[c][0]
        for k in K:
            ol = orders[k].req_level
            if not (cl == ol or cl == ol + 1):
                m.addConstr(assign[c, k] == 0)

    for c in C:
        for k in K:
            m.addConstr(start[c, k] <= assign[c, k])
            m.addConstr(end_v[c, k] <= assign[c, k])

    for c in C:
        m.addConstr(gp.quicksum(start[c, k] for k in K) <= 1)
        m.addConstr(gp.quicksum(end_v[c, k] for k in K) <= 1)

    for c in C:
        for k in K:
            incoming = gp.quicksum(z[c, i, k] for i in arcs_in[k])
            outgoing = gp.quicksum(z[c, k, j] for j in arcs_out[k])
            m.addConstr(start[c, k] + incoming == assign[c, k])
            m.addConstr(end_v[c, k] + outgoing == assign[c, k])

    for c in C:
        m.addConstr(
            gp.quicksum(start[c, k] for k in K) == gp.quicksum(end_v[c, k] for k in K)
        )

    for c in C:
        for k in K:
            m.addConstr(gp.quicksum(z[c, i, k] for i in arcs_in[k]) <= 1)
            m.addConstr(gp.quicksum(z[c, k, j] for j in arcs_out[k]) <= 1)

    caps = compute_per_car_order_caps(path)
    for c in C:
        m.addConstr(gp.quicksum(assign[c, k] for k in K) <= caps[c])

    travel_between = gp.quicksum(
        T[(orders[i].rs, orders[j].ps)] * z[c, i, j] for c in C for (i, j) in feasible_arcs
    )
    initial_travel = gp.quicksum(
        T[(cars[c][1], orders[k].ps)] * start[c, k] for c in C for k in K
    )
    m.addConstr(travel_between + initial_travel <= int(B))

    profit = gp.quicksum(R[k] * x[k] - 2 * R[k] * (1 - x[k]) for k in K)
    m.setObjective(profit, GRB.MAXIMIZE)

    return m, K, C, R, x, assign, start, end_v, z, orders, cars, T, B


def solve_optimal_gurobi(path: str, time_limit_s: float = 20.0) -> MIPResult:
    """
    Solve the instance with a MIP (Gurobi), adapted from problem_1_code.py.
    Returns MIPResult with objective, MIP gap when applicable, and revenue /
    compensation / accepted order count when an incumbent solution exists.
    """
    m, K, C, R, x, assign, start, end_v, z, orders, cars, T, B = _build_car_rental_model(
        path, continuous_relaxation=False
    )
    m.Params.TimeLimit = float(time_limit_s)

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


def solve_lp_relaxation_gurobi(path: str, time_limit_s: float = 45.0) -> Optional[float]:
    """
    LP relaxation of the same MIP: all binary variables relaxed to [0,1].
    For a maximization problem, the LP optimum is a valid upper bound on the integer optimum.
    Returns min(lp_obj, sum_k R_k) when a primal objective is available, else None.
    """
    try:
        m, K, _C, R, x, *_ = _build_car_rental_model(path, continuous_relaxation=True)
        m.Params.TimeLimit = float(time_limit_s)
        m.Params.Method = GRB.METHOD_AUTO
        m.optimize()
    except gp.GurobiError:
        return None

    sum_r = float(sum(R[k] for k in K))
    if m.Status == GRB.OPTIMAL:
        return min(float(m.ObjVal), sum_r)
    if m.Status in (GRB.TIME_LIMIT, GRB.SUBOPTIMAL) and m.SolCount > 0:
        return min(float(m.ObjVal), sum_r)
    if m.SolCount > 0:
        return min(float(m.ObjVal), sum_r)
    return None


def solve_lp_relaxation_pulp(path: str, time_limit_s: float = 60.0) -> Optional[float]:
    """
    Same LP relaxation as Gurobi, solved with PuLP + CBC (no Gurobi size-limited license).
    Optional dependency: pip install pulp
    """
    try:
        import pulp
    except ImportError:
        return None

    try:
        K, C, R, feasible_arcs, arcs_in, arcs_out, orders, cars, T, B = _car_rental_instance_data(path)
        sum_r = float(sum(R[k] for k in K))

        prob = pulp.LpProblem("car_rental_lp_pulp", pulp.LpMaximize)

        x = {k: pulp.LpVariable(f"x_{k}", lowBound=0, upBound=1) for k in K}
        assign = {
            (c, k): pulp.LpVariable(f"a_{c}_{k}", lowBound=0, upBound=1) for c in C for k in K
        }
        start = {
            (c, k): pulp.LpVariable(f"s_{c}_{k}", lowBound=0, upBound=1) for c in C for k in K
        }
        end_v = {
            (c, k): pulp.LpVariable(f"e_{c}_{k}", lowBound=0, upBound=1) for c in C for k in K
        }
        z = {
            (c, i, j): pulp.LpVariable(f"z_{c}_{i}_{j}", lowBound=0, upBound=1)
            for c in C
            for (i, j) in feasible_arcs
        }

        for c in C:
            init_station = cars[c][1]
            for k in K:
                arrival = PLANNING_START + timedelta(minutes=T[(init_station, orders[k].ps)])
                deadline = orders[k].pt - timedelta(minutes=30)
                if deadline < PLANNING_START:
                    deadline = PLANNING_START
                if arrival > deadline:
                    prob += start[c, k] == 0

        for k in K:
            prob += pulp.lpSum(assign[c, k] for c in C) == x[k]

        for c in C:
            cl = cars[c][0]
            for k in K:
                ol = orders[k].req_level
                if not (cl == ol or cl == ol + 1):
                    prob += assign[c, k] == 0

        for c in C:
            for k in K:
                prob += start[c, k] <= assign[c, k]
                prob += end_v[c, k] <= assign[c, k]

        for c in C:
            prob += pulp.lpSum(start[c, k] for k in K) <= 1
            prob += pulp.lpSum(end_v[c, k] for k in K) <= 1

        for c in C:
            for k in K:
                incoming = pulp.lpSum(z[c, i, k] for i in arcs_in[k])
                outgoing = pulp.lpSum(z[c, k, j] for j in arcs_out[k])
                prob += start[c, k] + incoming == assign[c, k]
                prob += end_v[c, k] + outgoing == assign[c, k]

        for c in C:
            prob += pulp.lpSum(start[c, k] for k in K) == pulp.lpSum(end_v[c, k] for k in K)

        for c in C:
            for k in K:
                prob += pulp.lpSum(z[c, i, k] for i in arcs_in[k]) <= 1
                prob += pulp.lpSum(z[c, k, j] for j in arcs_out[k]) <= 1

        caps = compute_per_car_order_caps(path)
        for c in C:
            prob += pulp.lpSum(assign[c, k] for k in K) <= caps[c]

        travel_between = pulp.lpSum(
            T[(orders[i].rs, orders[j].ps)] * z[c, i, j] for c in C for (i, j) in feasible_arcs
        )
        initial_travel = pulp.lpSum(
            T[(cars[c][1], orders[k].ps)] * start[c, k] for c in C for k in K
        )
        prob += travel_between + initial_travel <= int(B)

        prob += pulp.lpSum(R[k] * x[k] - 2 * R[k] * (1 - x[k]) for k in K)

        solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=int(max(1.0, time_limit_s)))
        prob.solve(solver)

        if prob.status != pulp.LpStatusOptimal:
            return None
        obj = pulp.value(prob.objective)
        if obj is None:
            return None
        return min(float(obj), sum_r)
    except Exception:
        return None

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
    lp_time_limit_s: float = 45.0,
    output_tag: Optional[str] = None,
    output_dir: str = "analysis_outputs",
) -> None:
    """
    Run heuristic + MIP benchmark on all matching instances; write unified CSVs under
    ``output_dir``. See docs/EXPERIMENT_REPORT.md for column definitions.
    """
    files = sorted(glob.glob(instance_glob))
    if not files:
        raise SystemExit(
            f"No files matched {instance_glob!r}. Unzip generated_instances_v2.zip if needed."
        )

    os.makedirs(output_dir, exist_ok=True)

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
            lp_ub: Optional[float] = None
            lp_tag = ""
            try:
                lp_ub = solve_lp_relaxation_gurobi(
                    fp,
                    time_limit_s=max(25.0, float(lp_time_limit_s), float(mip_time_limit_s) * 1.5),
                )
                if lp_ub is not None:
                    lp_tag = "LP_RELAX"
            except gp.GurobiError:
                lp_ub = None
            except Exception:
                lp_ub = None
            if lp_ub is None:
                try:
                    lp_ub = solve_lp_relaxation_pulp(
                        fp,
                        time_limit_s=max(60.0, float(lp_time_limit_s), float(mip_time_limit_s) * 2.0),
                    )
                    if lp_ub is not None:
                        lp_tag = "LP_RELAX_CBC"
                except Exception:
                    lp_ub = None

            card_ub = profit_upper_bound_cardinality(fp)
            accept_ub = profit_upper_bound_accept_all(fp)
            cand_lp = float(lp_ub) if lp_ub is not None else None
            ub_profit = min(
                [x for x in (cand_lp, float(card_ub), float(accept_ub)) if x is not None]
            )
            if cand_lp is not None and abs(ub_profit - cand_lp) <= 1e-5:
                ub_type = lp_tag
            elif abs(ub_profit - float(card_ub)) <= 1e-5:
                ub_type = "CARD_UB"
            else:
                ub_type = "ACCEPT_ALL_UB"

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
    out_experiment = _tagged_output_path(
        os.path.join(output_dir, "experiment_report.csv"), output_tag
    )
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
    out_summary = _tagged_output_path(
        os.path.join(output_dir, "summary_by_scenario.csv"), output_tag
    )
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
            _tagged_output_path(os.path.join(output_dir, f"profit_hist_{sc}.png"), output_tag),
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
            _tagged_output_path(
                os.path.join(output_dir, "profit_boxplot_by_scenario.png"), output_tag
            ),
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
        _tagged_output_path(
            os.path.join(output_dir, "feasible_rate_by_scenario.png"), output_tag
        ),
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
            _tagged_output_path(
                os.path.join(output_dir, f"gap_to_opt_hist_{sc}.png"), output_tag
            ),
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
            _tagged_output_path(
                os.path.join(output_dir, f"gap_to_ub_hist_{sc}.png"), output_tag
            ),
            dpi=200,
        )
        plt.close()

    print(f"Wrote tagged outputs under {output_dir!r} (tag={output_tag!r})")
    print(summary)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(
        description="Run full experiment (heuristic + MIP) and write CSV/PNGs under output-dir."
    )
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
        help="Gurobi MIP time limit per instance (seconds)",
    )
    ap.add_argument(
        "--lp-time-limit",
        type=float,
        default=45.0,
        help="Gurobi LP relaxation time limit when MIP yields no objective (seconds)",
    )
    ap.add_argument(
        "--tag",
        default=None,
        help="Suffix for output files, e.g. v2.1",
    )
    ap.add_argument(
        "--output-dir",
        default="analysis_outputs",
        help="Directory for experiment_report*.csv, summary*.csv, and optional PNGs",
    )
    args = ap.parse_args()
    run_experiment(
        instance_glob=args.glob,
        write_plots=not args.no_plots,
        mip_time_limit_s=args.mip_time_limit,
        lp_time_limit_s=args.lp_time_limit,
        output_tag=args.tag,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()

