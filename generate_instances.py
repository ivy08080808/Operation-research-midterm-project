#!/usr/bin/env python3
"""
Random instance generator (course spec §5.1–5.2).

Produces **50 instances**: 5 scenarios × 10 repeats each. Each file has the
required five blocks (header params, cars, hourly rates, orders, moving times).

Generation rules (aligned with project PDF):
  - Per-scenario fixed n_S, n_C, n_L, n_K, n_D, B, fleet composition, rates, and
    order-level / station distributions.
  - Each car's initial station is uniform over stations.
  - Pick-up times on half-hour grid within the horizon; rental duration resampled
    until return time lies within the planning horizon.
  - Moving times: symmetric, T_ii=0, integer multiples of 30 minutes.

Two scales:
  - **small** — TA-scale instances → ``generated_instances_small/`` (default out).
  - **big** — larger n_S, n_C, n_K, n_D, etc. → ``generated_instances_big/``.

Legacy aliases: ``hard`` → small, ``ultra`` → big.

Usage:
  python3 generate_instances.py --scale small
  python3 generate_instances.py --scale big --out-dir generated_instances_big
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime, timedelta


def format_time(dt):
    return dt.strftime("%Y/%m/%d %H:%M")


def random_pickup_time(start_date, n_D):
    """
    Generate a random pick-up time within the planning horizon.
    Time must be either x:00 or x:30.
    """
    total_half_hours = n_D * 24 * 2
    slot = random.randint(0, total_half_hours - 1)
    return start_date + timedelta(minutes=30 * slot)


def generate_moving_times(n_S, min_time=30, max_time=150):
    """
    Generate symmetric moving times.
    T_ii = 0
    T_ij = T_ji
    Moving time (minutes) must be a multiple of 30 (course PDF); callers must pass
    min_time / max_time that are already multiples of 30 so randrange(..., step=30)
    only hits ..., 60, 90, ... (e.g. min_time=45 would wrongly allow 45, 75, ...).
    """
    if min_time % 30 != 0 or max_time % 30 != 0:
        raise ValueError(
            f"min_time and max_time must be multiples of 30, got min_time={min_time}, max_time={max_time}"
        )
    moving_times = {}

    for i in range(1, n_S + 1):
        moving_times[(i, i)] = 0

    for i in range(1, n_S + 1):
        for j in range(i + 1, n_S + 1):
            t = random.randrange(min_time, max_time + 1, 30)
            moving_times[(i, j)] = t
            moving_times[(j, i)] = t

    return moving_times


def generate_instance(
    filename,
    n_S,
    n_C,
    n_L,
    n_K,
    n_D,
    B,
    car_counts,
    hourly_rates,
    order_level_probs,
    pickup_station_probs,
    return_station_probs,
    min_duration_hours=1,
    max_duration_hours=12,
    min_moving_time=30,
    max_moving_time=150,
    seed=None,
):
    if seed is not None:
        random.seed(seed)

    start_date = datetime(2023, 1, 1, 0, 0)
    end_date = start_date + timedelta(days=n_D)

    stations = list(range(1, n_S + 1))
    levels = list(range(1, n_L + 1))

    # Generate cars with fixed fleet composition
    cars = []
    car_id = 1
    for level, count in enumerate(car_counts, start=1):
        for _ in range(count):
            initial_station = random.choice(stations)
            cars.append((car_id, level, initial_station))
            car_id += 1

    # Generate orders
    orders = []
    for order_id in range(1, n_K + 1):
        order_level = random.choices(levels, weights=order_level_probs, k=1)[0]
        pickup_station = random.choices(stations, weights=pickup_station_probs, k=1)[0]
        return_station = random.choices(stations, weights=return_station_probs, k=1)[0]

        while True:
            pickup_time = random_pickup_time(start_date, n_D)
            duration_hours = random.randint(min_duration_hours, max_duration_hours)
            return_time = pickup_time + timedelta(hours=duration_hours)

            if return_time <= end_date:
                break

        orders.append(
            (
                order_id,
                order_level,
                pickup_station,
                return_station,
                format_time(pickup_time),
                format_time(return_time),
            )
        )

    # Generate moving times
    moving_times = generate_moving_times(
        n_S,
        min_time=min_moving_time,
        max_time=max_moving_time,
    )

    # Write txt file
    with open(filename, "w", encoding="utf-8") as f:
        f.write("n_S,n_C,n_L,n_K,n_D,B\n")
        f.write(f"{n_S},{n_C},{n_L},{n_K},{n_D},{B}\n")
        f.write("==========\n")

        f.write("Car ID,Level,Initial station\n")
        for car in cars:
            f.write(",".join(map(str, car)) + "\n")
        f.write("==========\n")

        f.write("Car level,Hour rate\n")
        for level, rate in enumerate(hourly_rates, start=1):
            f.write(f"{level},{rate}\n")
        f.write("==========\n")

        f.write("Order ID,Level,Pick-up station,Return station,Pick-up time,Return time\n")
        for order in orders:
            f.write(",".join(map(str, order)) + "\n")
        f.write("==========\n")

        f.write("From,To,Moving time\n")
        for i in range(1, n_S + 1):
            for j in range(1, n_S + 1):
                f.write(f"{i},{j},{moving_times[(i, j)]}\n")
        f.write("==========\n")


def _uniform_station_probs(n_s: int):
    p = 1.0 / float(n_s)
    return [p] * n_s


def _hub_pick_return_probs(n_s: int, hub_pick: int, hub_ret: int, hub_w: int, rim_w: int):
    """Pickups cluster at hub_pick; returns cluster at hub_ret (1-based indices)."""
    pick = [rim_w] * n_s
    pick[hub_pick - 1] = hub_w
    sp = sum(pick)
    pick_p = [x / sp for x in pick]
    ret = [rim_w] * n_s
    ret[hub_ret - 1] = hub_w
    sr = sum(ret)
    ret_p = [x / sr for x in ret]
    return pick_p, ret_p


def _normalize_scale(scale: str) -> str:
    s = (scale or "small").strip().lower()
    if s in ("hard", "small"):
        return "small"
    if s in ("ultra", "big"):
        return "big"
    raise ValueError(f"Unknown scale {scale!r}; use small|big (aliases: hard|ultra)")


def main(scale: str = "small", *, output_dir: str | None = None) -> str:
    """
    Write 50 instances under output_dir. Returns the directory used.
    """
    which = _normalize_scale(scale)
    if output_dir is None:
        output_dir = "generated_instances_small" if which == "small" else "generated_instances_big"

    os.makedirs(output_dir, exist_ok=True)

    if which == "small":
        n_s = 8
        u_st = _uniform_station_probs(n_s)
        s3_pick_p, s3_ret_p = _hub_pick_return_probs(n_s, 1, 8, 12, 2)
        scenarios = {
            "S1_baseline": {
                "n_S": n_s,
                "n_C": 18,
                "n_L": 3,
                "n_K": 36,
                "n_D": 7,
                "B": 920,
                "car_counts": [7, 7, 4],
                "hourly_rates": [140, 320, 820],
                "order_level_probs": [0.33, 0.33, 0.34],
                "pickup_station_probs": u_st,
                "return_station_probs": u_st,
                "min_duration_hours": 1,
                "max_duration_hours": 14,
                "min_moving_time": 60,
                "max_moving_time": 210,
            },
            "S2_high_low_level_demand": {
                "n_S": n_s,
                "n_C": 18,
                "n_L": 3,
                "n_K": 36,
                "n_D": 7,
                "B": 920,
                "car_counts": [7, 7, 4],
                "hourly_rates": [140, 320, 820],
                "order_level_probs": [0.70, 0.20, 0.10],
                "pickup_station_probs": u_st,
                "return_station_probs": u_st,
                "min_duration_hours": 1,
                "max_duration_hours": 14,
                "min_moving_time": 60,
                "max_moving_time": 210,
            },
            "S3_geographic_imbalance": {
                "n_S": n_s,
                "n_C": 18,
                "n_L": 3,
                "n_K": 36,
                "n_D": 7,
                "B": 920,
                "car_counts": [7, 7, 4],
                "hourly_rates": [140, 320, 820],
                "order_level_probs": [0.33, 0.33, 0.34],
                "pickup_station_probs": s3_pick_p,
                "return_station_probs": s3_ret_p,
                "min_duration_hours": 1,
                "max_duration_hours": 14,
                "min_moving_time": 60,
                "max_moving_time": 210,
            },
            "S4_high_order_load": {
                "n_S": n_s,
                "n_C": 18,
                "n_L": 3,
                "n_K": 42,
                "n_D": 7,
                "B": 1040,
                "car_counts": [7, 7, 4],
                "hourly_rates": [140, 320, 820],
                "order_level_probs": [0.33, 0.33, 0.34],
                "pickup_station_probs": u_st,
                "return_station_probs": u_st,
                "min_duration_hours": 1,
                "max_duration_hours": 10,
                "min_moving_time": 60,
                "max_moving_time": 210,
            },
            "S5_tight_relo_budget": {
                "n_S": n_s,
                "n_C": 18,
                "n_L": 3,
                "n_K": 36,
                "n_D": 7,
                "B": 260,
                "car_counts": [7, 7, 4],
                "hourly_rates": [140, 320, 820],
                "order_level_probs": [0.33, 0.33, 0.34],
                "pickup_station_probs": u_st,
                "return_station_probs": u_st,
                "min_duration_hours": 1,
                "max_duration_hours": 12,
                "min_moving_time": 30,
                "max_moving_time": 180,
            },
        }
        seed_base = 5000
    else:
        n_s = 11
        u_st = _uniform_station_probs(n_s)
        s3_pick_p, s3_ret_p = _hub_pick_return_probs(n_s, 1, 11, 14, 2)
        scenarios = {
            "S1_baseline": {
                "n_S": n_s,
                "n_C": 30,
                "n_L": 3,
                "n_K": 58,
                "n_D": 10,
                "B": 1580,
                "car_counts": [12, 10, 8],
                "hourly_rates": [190, 440, 1080],
                "order_level_probs": [0.33, 0.33, 0.34],
                "pickup_station_probs": u_st,
                "return_station_probs": u_st,
                "min_duration_hours": 1,
                "max_duration_hours": 18,
                "min_moving_time": 60,
                "max_moving_time": 270,
            },
            "S2_high_low_level_demand": {
                "n_S": n_s,
                "n_C": 30,
                "n_L": 3,
                "n_K": 58,
                "n_D": 10,
                "B": 1580,
                "car_counts": [12, 10, 8],
                "hourly_rates": [190, 440, 1080],
                "order_level_probs": [0.70, 0.20, 0.10],
                "pickup_station_probs": u_st,
                "return_station_probs": u_st,
                "min_duration_hours": 1,
                "max_duration_hours": 18,
                "min_moving_time": 60,
                "max_moving_time": 270,
            },
            "S3_geographic_imbalance": {
                "n_S": n_s,
                "n_C": 30,
                "n_L": 3,
                "n_K": 58,
                "n_D": 10,
                "B": 1580,
                "car_counts": [12, 10, 8],
                "hourly_rates": [190, 440, 1080],
                "order_level_probs": [0.33, 0.33, 0.34],
                "pickup_station_probs": s3_pick_p,
                "return_station_probs": s3_ret_p,
                "min_duration_hours": 1,
                "max_duration_hours": 18,
                "min_moving_time": 60,
                "max_moving_time": 270,
            },
            "S4_high_order_load": {
                "n_S": n_s,
                "n_C": 30,
                "n_L": 3,
                "n_K": 118,
                "n_D": 10,
                "B": 1580,
                "car_counts": [12, 10, 8],
                "hourly_rates": [190, 440, 1080],
                "order_level_probs": [0.33, 0.33, 0.34],
                "pickup_station_probs": u_st,
                "return_station_probs": u_st,
                "min_duration_hours": 1,
                "max_duration_hours": 10,
                "min_moving_time": 60,
                "max_moving_time": 270,
            },
            "S5_tight_relo_budget": {
                "n_S": n_s,
                "n_C": 30,
                "n_L": 3,
                "n_K": 58,
                "n_D": 10,
                "B": 360,
                "car_counts": [12, 10, 8],
                "hourly_rates": [190, 440, 1080],
                "order_level_probs": [0.33, 0.33, 0.34],
                "pickup_station_probs": u_st,
                "return_station_probs": u_st,
                "min_duration_hours": 1,
                "max_duration_hours": 14,
                "min_moving_time": 30,
                "max_moving_time": 210,
            },
        }
        seed_base = 7000

    for scenario_index, (scenario_name, params) in enumerate(scenarios.items()):
        for idx in range(1, 11):
            filename = os.path.join(output_dir, f"{scenario_name}_{idx:02d}.txt")
            generate_instance(
                filename=filename,
                seed=seed_base + scenario_index * 100 + idx,
                **params,
            )
            print(f"Generated {filename}")

    print(f"Done. Generated 50 instances under {output_dir}/ (scale={which})")
    return output_dir


def _cli(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Generate 50 random instances (5 scenarios × 10).")
    ap.add_argument(
        "--scale",
        choices=("small", "big", "hard", "ultra"),
        default="small",
        help="small (alias hard) or big (alias ultra); sets default output directory.",
    )
    ap.add_argument(
        "--out-dir",
        default=None,
        help="Output directory (default: generated_instances_small or generated_instances_big).",
    )
    args = ap.parse_args(argv)
    main(args.scale, output_dir=args.out_dir)


if __name__ == "__main__":
    _cli(sys.argv[1:])
