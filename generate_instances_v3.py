import random
from datetime import datetime, timedelta
import os


def format_time(dt):
    return dt.strftime("%Y/%m/%d %H:%M")


def random_pickup_time(start_date, start_day, end_day):
    window_start = start_date + timedelta(days=start_day - 1)
    window_end = start_date + timedelta(days=end_day)
    total_half_hours = int((window_end - window_start).total_seconds() // (30 * 60))
    slot = random.randint(0, total_half_hours - 1)
    return window_start + timedelta(minutes=30 * slot)


def generate_moving_times(n_S, min_time, max_time):
    moving_times = {}
    for i in range(1, n_S + 1):
        moving_times[(i, i)] = 0
    for i in range(1, n_S + 1):
        for j in range(i + 1, n_S + 1):
            t = random.randrange(min_time, max_time + 1, 30)
            moving_times[(i, j)] = t
            moving_times[(j, i)] = t
    return moving_times


def normalize(probs):
    total = sum(probs)
    return [p / total for p in probs]


def uniform_probs(n):
    return [1 / n] * n


def grouped_probs(n, main_stations, main_total_prob):
    probs = [0.0] * n
    main_set = set(main_stations)
    other_count = n - len(main_set)

    for s in main_stations:
        probs[s - 1] = main_total_prob / len(main_set)

    if other_count > 0:
        other_prob = (1 - main_total_prob) / other_count
        for s in range(1, n + 1):
            if s not in main_set:
                probs[s - 1] = other_prob
    return probs


def generate_clustered_station_probs(n_S, cluster_size=4, cluster_total_prob=0.48, start_cluster=True):
    if start_cluster:
        main_stations = list(range(1, cluster_size + 1))
    else:
        main_stations = list(range(n_S - cluster_size + 1, n_S + 1))
    return grouped_probs(n_S, main_stations, cluster_total_prob)


def generate_instance(
    filename, n_S, n_C, n_L, n_K, n_D, B, car_counts, hourly_rates,
    order_level_probs, pickup_station_probs, return_station_probs,
    pickup_start_day, pickup_end_day, min_duration_hours, max_duration_hours,
    min_moving_time, max_moving_time, seed=None
):
    if seed is not None:
        random.seed(seed)

    start_date = datetime(2023, 1, 1, 0, 0)
    end_date = start_date + timedelta(days=n_D)

    stations = list(range(1, n_S + 1))
    levels = list(range(1, n_L + 1))

    if len(car_counts) != n_L:
        raise ValueError("car_counts length must match n_L.")
    if sum(car_counts) != n_C:
        raise ValueError("sum(car_counts) must match n_C.")
    if len(hourly_rates) != n_L:
        raise ValueError("hourly_rates length must match n_L.")
    if len(order_level_probs) != n_L:
        raise ValueError("order_level_probs length must match n_L.")
    if len(pickup_station_probs) != n_S:
        raise ValueError("pickup_station_probs length must match n_S.")
    if len(return_station_probs) != n_S:
        raise ValueError("return_station_probs length must match n_S.")

    order_level_probs = normalize(order_level_probs)
    pickup_station_probs = normalize(pickup_station_probs)
    return_station_probs = normalize(return_station_probs)

    cars = []
    car_id = 1
    for level, count in enumerate(car_counts, start=1):
        for _ in range(count):
            initial_station = random.choice(stations)
            cars.append((car_id, level, initial_station))
            car_id += 1

    orders = []
    for order_id in range(1, n_K + 1):
        order_level = random.choices(levels, weights=order_level_probs, k=1)[0]
        pickup_station = random.choices(stations, weights=pickup_station_probs, k=1)[0]
        return_station = random.choices(stations, weights=return_station_probs, k=1)[0]

        while True:
            pickup_time = random_pickup_time(start_date, pickup_start_day, pickup_end_day)
            duration_hours = random.randint(min_duration_hours, max_duration_hours)
            return_time = pickup_time + timedelta(hours=duration_hours)
            if return_time <= end_date:
                break

        orders.append((order_id, order_level, pickup_station, return_station,
                       format_time(pickup_time), format_time(return_time)))

    moving_times = generate_moving_times(n_S, min_time=min_moving_time, max_time=max_moving_time)

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


if __name__ == "__main__":
    output_dir = "generated_instances_v3"
    os.makedirs(output_dir, exist_ok=True)

    scenarios = {
        "S1_baseline": {
            "n_S": 14, "n_C": 45, "n_L": 3, "n_K": 145, "n_D": 8, "B": 6000,
            "car_counts": [18, 18, 9],
            "hourly_rates": [100, 300, 500],
            "order_level_probs": [0.40, 0.40, 0.20],
            "pickup_station_probs": uniform_probs(14),
            "return_station_probs": uniform_probs(14),
            "pickup_start_day": 1, "pickup_end_day": 7,
            "min_duration_hours": 2, "max_duration_hours": 16,
            "min_moving_time": 60, "max_moving_time": 240,
        },
        "S2_high_low_level_demand": {
            "n_S": 16, "n_C": 54, "n_L": 3, "n_K": 145, "n_D": 8, "B": 8400,
            "car_counts": [22, 22, 10],
            "hourly_rates": [100, 300, 600],
            "order_level_probs": [0.70, 0.20, 0.10],
            "pickup_station_probs": uniform_probs(16),
            "return_station_probs": uniform_probs(16),
            "pickup_start_day": 1, "pickup_end_day": 7,
            "min_duration_hours": 2, "max_duration_hours": 16,
            "min_moving_time": 60, "max_moving_time": 240,
        },
        "S3_geographic_imbalance": {
            "n_S": 18, "n_C": 60, "n_L": 3, "n_K": 150, "n_D": 9, "B": 12000,
            "car_counts": [24, 24, 12],
            "hourly_rates": [100, 300, 500],
            "order_level_probs": [0.40, 0.40, 0.20],
            "pickup_station_probs": generate_clustered_station_probs(18, cluster_size=4, cluster_total_prob=0.48, start_cluster=True),
            "return_station_probs": generate_clustered_station_probs(18, cluster_size=4, cluster_total_prob=0.48, start_cluster=False),
            "pickup_start_day": 1, "pickup_end_day": 8,
            "min_duration_hours": 2, "max_duration_hours": 14,
            "min_moving_time": 60, "max_moving_time": 240,
        },
        "S4_high_order_load": {
            "n_S": 20, "n_C": 72, "n_L": 3, "n_K": 150, "n_D": 9, "B": 15000,
            "car_counts": [29, 29, 14],
            "hourly_rates": [100, 300, 500],
            "order_level_probs": [0.35, 0.35, 0.30],
            "pickup_station_probs": uniform_probs(20),
            "return_station_probs": uniform_probs(20),
            "pickup_start_day": 1, "pickup_end_day": 8,
            "min_duration_hours": 2, "max_duration_hours": 14,
            "min_moving_time": 60, "max_moving_time": 210,
        },
        "S5_tight_relo_budget": {
            "n_S": 20, "n_C": 72, "n_L": 4, "n_K": 190, "n_D": 10, "B": 4500,
            "car_counts": [26, 22, 15, 9],
            "hourly_rates": [100, 300, 500, 900],
            "order_level_probs": [0.30, 0.25, 0.25, 0.20],
            "pickup_station_probs": uniform_probs(20),
            "return_station_probs": uniform_probs(20),
            "pickup_start_day": 1, "pickup_end_day": 9,
            "min_duration_hours": 3, "max_duration_hours": 18,
            "min_moving_time": 90, "max_moving_time": 300,
        },
    }

    for scenario_index, (scenario_name, params) in enumerate(scenarios.items()):
        for idx in range(1, 11):
            filename = os.path.join(output_dir, f"{scenario_name}_{idx:02d}.txt")
            generate_instance(filename=filename, seed=4900 + scenario_index * 100 + idx, **params)
            print(f"Generated {filename}")

    print("Done. Generated 50 final v3 instances.")
