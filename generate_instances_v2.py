import random
from datetime import datetime, timedelta
import os


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
    Moving time is a multiple of 30.
    """
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
    seed=None
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

        orders.append((
            order_id,
            order_level,
            pickup_station,
            return_station,
            format_time(pickup_time),
            format_time(return_time)
        ))

    # Generate moving times
    moving_times = generate_moving_times(
        n_S,
        min_time=min_moving_time,
        max_time=max_moving_time
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


if __name__ == "__main__":
    output_dir = "generated_instances_v2"
    os.makedirs(output_dir, exist_ok=True)

    scenarios = {
        "S1_baseline": {
            "n_S": 5,
            "n_C": 10,
            "n_L": 3,
            "n_K": 20,
            "n_D": 5,
            "B": 600,
            "car_counts": [4, 4, 2],
            "hourly_rates": [100, 200, 500],
            "order_level_probs": [0.33, 0.33, 0.34],
            "pickup_station_probs": [0.20, 0.20, 0.20, 0.20, 0.20],
            "return_station_probs": [0.20, 0.20, 0.20, 0.20, 0.20],
            "min_duration_hours": 1,
            "max_duration_hours": 12,
            "min_moving_time": 30,
            "max_moving_time": 150,
        },
        "S2_high_low_level_demand": {
            "n_S": 5,
            "n_C": 10,
            "n_L": 3,
            "n_K": 20,
            "n_D": 5,
            "B": 600,
            "car_counts": [4, 4, 2],
            "hourly_rates": [100, 200, 500],
            "order_level_probs": [0.70, 0.20, 0.10],
            "pickup_station_probs": [0.20, 0.20, 0.20, 0.20, 0.20],
            "return_station_probs": [0.20, 0.20, 0.20, 0.20, 0.20],
            "min_duration_hours": 1,
            "max_duration_hours": 12,
            "min_moving_time": 30,
            "max_moving_time": 150,
        },
        "S3_geographic_imbalance": {
            "n_S": 5,
            "n_C": 10,
            "n_L": 3,
            "n_K": 20,
            "n_D": 5,
            "B": 600,
            "car_counts": [4, 4, 2],
            "hourly_rates": [100, 200, 500],
            "order_level_probs": [0.33, 0.33, 0.34],
            "pickup_station_probs": [0.60, 0.10, 0.10, 0.10, 0.10],
            "return_station_probs": [0.10, 0.10, 0.10, 0.10, 0.60],
            "min_duration_hours": 1,
            "max_duration_hours": 12,
            "min_moving_time": 30,
            "max_moving_time": 150,
        },
        "S4_high_order_load": {
            "n_S": 5,
            "n_C": 10,
            "n_L": 3,
            "n_K": 40,
            "n_D": 5,
            "B": 600,
            "car_counts": [4, 4, 2],
            "hourly_rates": [100, 200, 500],
            "order_level_probs": [0.33, 0.33, 0.34],
            "pickup_station_probs": [0.20, 0.20, 0.20, 0.20, 0.20],
            "return_station_probs": [0.20, 0.20, 0.20, 0.20, 0.20],
            "min_duration_hours": 1,
            "max_duration_hours": 8,
            "min_moving_time": 30,
            "max_moving_time": 150,
        },
        "S5_tight_relo_budget": {
            "n_S": 5,
            "n_C": 10,
            "n_L": 3,
            "n_K": 20,
            "n_D": 5,
            "B": 150,
            "car_counts": [4, 4, 2],
            "hourly_rates": [100, 200, 500],
            "order_level_probs": [0.33, 0.33, 0.34],
            "pickup_station_probs": [0.20, 0.20, 0.20, 0.20, 0.20],
            "return_station_probs": [0.20, 0.20, 0.20, 0.20, 0.20],
            "min_duration_hours": 1,
            "max_duration_hours": 10,
            "min_moving_time": 30,
            "max_moving_time": 120,
        },
    }

    # Generate 10 instances for each scenario
    for scenario_index, (scenario_name, params) in enumerate(scenarios.items()):
        for idx in range(1, 11):
            filename = os.path.join(output_dir, f"{scenario_name}_{idx:02d}.txt")
            generate_instance(
                filename=filename,
                seed=2000 + scenario_index * 100 + idx,
                **params
            )
            print(f"Generated {filename}")

    print("Done. Generated 50 instances.")
