import glob
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import matplotlib.pyplot as plt
import pandas as pd

import algorithm_module


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


def baseline_greedy_with_relocation(path: str):
    """
    Baseline heuristic (still simple, but not too weak):
    - Single greedy pass (no multi-start, no BFS reassignment)
    - Allows relocation subject to remaining budget B
    - Allows free upgrade: request level l can use car level l or l+1
    - Orders are processed by revenue (desc), tie by earlier pickup time
    """
    nS, nC, nL, nK, nD, B, cars, rate, orders, T = load_instance(path)

    # precompute revenue to rank orders
    R = compute_R(orders, rate)
    order_ids = list(orders.keys())
    order_ids.sort(key=lambda oid: (-R[oid], orders[oid].pt, oid))

    # car state
    car_level = {cid: cars[cid][0] for cid in cars}
    car_station = {cid: cars[cid][1] for cid in cars}
    car_ready = {cid: PLANNING_START for cid in cars}

    budget_left = int(B)

    assignment = [-1] * nK
    relocation = []

    # nearby stations list (by travel time into pickup station)
    nearby = {s: list(range(1, nS + 1)) for s in range(1, nS + 1)}
    for s in range(1, nS + 1):
        nearby[s].sort(key=lambda j: T[(j, s)])  # from j -> s

    m_near_local = min(8, nS)

    for oid in order_ids:
        o = orders[oid]
        deadline = o.pt - timedelta(minutes=30)
        if deadline < PLANNING_START:
            deadline = PLANNING_START

        best = None
        # candidate: (move_minutes, upgrade_flag, arrive_time, from_station, car_id)

        # Try same-station first (move=0)
        for cid in cars:
            if car_station[cid] != o.ps:
                continue
            lvl = car_level[cid]
            if not (lvl == o.req_level or lvl == o.req_level + 1):
                continue
            if car_ready[cid] <= deadline:
                cand = (0, 0 if lvl == o.req_level else 1, car_ready[cid], o.ps, cid)
                if best is None or cand < best:
                    best = cand

        # Try relocation from nearby stations
        if best is None and budget_left > 0:
            for from_s in nearby[o.ps][:m_near_local]:
                if from_s == o.ps:
                    continue
                move_minutes = T[(from_s, o.ps)]
                if move_minutes <= 0 or move_minutes > budget_left:
                    continue

                for cid in cars:
                    if car_station[cid] != from_s:
                        continue
                    lvl = car_level[cid]
                    if not (lvl == o.req_level or lvl == o.req_level + 1):
                        continue
                    arrive = car_ready[cid] + timedelta(minutes=move_minutes)
                    if arrive <= deadline:
                        cand = (
                            move_minutes,
                            0 if lvl == o.req_level else 1,
                            arrive,
                            from_s,
                            cid,
                        )
                        if best is None or (cand[0], cand[1], cand[2]) < (best[0], best[1], best[2]):
                            best = cand

        if best is None:
            continue

        move_minutes, _up, _arrive, from_s, cid = best

        # record relocation if needed (start at car_ready time)
        if from_s != o.ps:
            relocation.append([int(cid), int(from_s), int(o.ps), fmt_time(car_ready[cid])])
            budget_left -= int(move_minutes)
            car_station[cid] = o.ps

        assignment[oid - 1] = int(cid)

        # update car after order
        car_station[cid] = o.rs
        car_ready[cid] = o.rt + timedelta(hours=4)

    return assignment, relocation


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
        try:
            # main heuristic
            assignment, relocation = algorithm_module.heuristic_algorithm(fp)
            feas, profit, info = evaluate(fp, assignment, relocation)

            # baseline (single-pass greedy with relocation)
            b_assignment, b_relocation = baseline_greedy_with_relocation(fp)
            b_feas, b_profit, b_info = evaluate(fp, b_assignment, b_relocation)

            err = "" if feas else info
            b_err = "" if b_feas else b_info

            # gap vs baseline (profit based)
            if profit is None or b_profit is None:
                gap = None
            else:
                denom = abs(b_profit) if abs(b_profit) > 1e-9 else 1.0
                gap = (profit - b_profit) / denom
        except Exception as e:
            feas, profit, err = False, None, repr(e)
            b_feas, b_profit, b_err, gap = False, None, "", None
        dt = time.time() - t0

        rows.append(
            {
                "file": fp,
                "scenario": scenario_from_filename(fp),
                "time_s": dt,
                "feasible": bool(feas),
                "profit": float(profit) if profit is not None else None,
                "baseline_feasible": bool(b_feas),
                "baseline_profit": float(b_profit) if b_profit is not None else None,
                "gap_vs_baseline": float(gap) if gap is not None else None,
                "error": err,
                "baseline_error": b_err,
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
            baseline_feasible_rate=("baseline_feasible", "mean"),
            profit_mean=("profit", "mean"),
            profit_std=("profit", "std"),
            baseline_profit_mean=("baseline_profit", "mean"),
            baseline_profit_std=("baseline_profit", "std"),
            gap_mean=("gap_vs_baseline", "mean"),
            gap_std=("gap_vs_baseline", "std"),
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

    # Plot: gap histogram per scenario (feasible only)
    for sc in scenarios:
        sub = df[
            (df["scenario"] == sc)
            & (df["feasible"] == True)
            & (df["baseline_feasible"] == True)
            & (df["gap_vs_baseline"].notna())
        ]
        if sub.empty:
            continue
        plt.figure(figsize=(7, 4))
        plt.hist(sub["gap_vs_baseline"], bins=10)
        plt.title(f"Gap vs baseline — {sc} (feasible only)")
        plt.xlabel("(profit - baseline_profit) / |baseline_profit|")
        plt.ylabel("Count")
        plt.tight_layout()
        plt.savefig(f"analysis_outputs/gap_hist_{sc}.png", dpi=200)
        plt.close()

    print("Wrote outputs to analysis_outputs/")
    print(summary)


if __name__ == "__main__":
    main()

