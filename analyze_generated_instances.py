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
            assignment, relocation = algorithm_module.heuristic_algorithm(fp)
            feas, profit, info = evaluate(fp, assignment, relocation)
            err = "" if feas else info
        except Exception as e:
            feas, profit, err = False, None, repr(e)
        dt = time.time() - t0

        rows.append(
            {
                "file": fp,
                "scenario": scenario_from_filename(fp),
                "time_s": dt,
                "feasible": bool(feas),
                "profit": float(profit) if profit is not None else None,
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

    print("Wrote outputs to analysis_outputs/")
    print(summary)


if __name__ == "__main__":
    main()

