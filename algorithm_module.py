from MTP_lib import *


def _parse_time(s: str) -> datetime:
    return datetime.strptime(s.strip(), "%Y/%m/%d %H:%M")


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%Y/%m/%d %H:%M")


def _hours_between(t1: datetime, t2: datetime) -> float:
    return (t2 - t1).total_seconds() / 3600.0


def _load_instance(file_path: str):
    """
    Parse instance file described in the project PDF.
    Returns:
      n_S, n_C, n_L, n_K, n_D, B,
      cars: dict car_id -> (level, station)
      rate: dict level -> hourly_rate
      orders: dict order_id -> (req_level, pickup_station, return_station, pickup_time_dt, return_time_dt)
      T: dict (i,j) -> minutes
    """
    with open(file_path, "r") as f:
        raw = [ln.strip() for ln in f if ln.strip() and "====" not in ln]

    idx = 0
    idx += 1  # general header
    n_S, n_C, n_L, n_K, n_D, B = map(int, raw[idx].split(","))
    idx += 1

    idx += 1  # car header
    cars = {}
    for _ in range(n_C):
        cid, lvl, st = map(int, raw[idx].split(","))
        cars[cid] = (lvl, st)
        idx += 1

    idx += 1  # rate header
    rate = {}
    for _ in range(n_L):
        lvl, r = map(int, raw[idx].split(","))
        rate[lvl] = r
        idx += 1

    idx += 1  # order header
    orders = {}
    for _ in range(n_K):
        parts = raw[idx].split(",")
        oid = int(parts[0])
        req_lvl = int(parts[1])
        ps = int(parts[2])
        rs = int(parts[3])
        pt = _parse_time(parts[4])
        rt = _parse_time(parts[5])
        orders[oid] = (req_lvl, ps, rs, pt, rt)
        idx += 1

    idx += 1  # travel header
    T = {}
    for _ in range(n_S * n_S):
        i, j, tmin = map(int, raw[idx].split(","))
        T[(i, j)] = tmin
        idx += 1

    return n_S, n_C, n_L, n_K, n_D, B, cars, rate, orders, T


def _sorted_nearby_stations(n_S: int, T: dict):
    """
    Precompute nearest-station lists for each station.
    Returns dict s -> [stations sorted by travel time from s (including itself first)].
    """
    nearby = {}
    for s in range(1, n_S + 1):
        lst = list(range(1, n_S + 1))
        lst.sort(key=lambda j: T[(s, j)])
        nearby[s] = lst
    return nearby


def heuristic_algorithm(file_path):
    """
    See comments in the example algorithm_module.py provided by course staff.
    Must return:
      assignment: list[int] length n_K, assignment[i-1] = car_id or -1
      relocation: list[list] each is [car_id, from_station, to_station, "YYYY/MM/DD hh:mm"]
    """
    n_S, n_C, n_L, n_K, n_D, B, cars_init, rate, orders, T = _load_instance(file_path)

    planning_start = datetime(2023, 1, 1, 0, 0)

    # Precompute revenue (for greedy tie-breaks)
    R = {}
    for oid, (lvl, _ps, _rs, pt, rt) in orders.items():
        R[oid] = rate[lvl] * _hours_between(pt, rt)

    # Car state (dynamic)
    car_level = {cid: cars_init[cid][0] for cid in cars_init}
    car_station = {cid: cars_init[cid][1] for cid in cars_init}
    car_available = {cid: planning_start for cid in cars_init}  # ready time at current station

    # Remaining relocation budget (minutes)
    budget_left = int(B)

    # Buckets by (station, level): each value is a list of (available_time, car_id) sorted ascending.
    buckets = {(s, l): [] for s in range(1, n_S + 1) for l in range(1, n_L + 1)}

    def bucket_push(s: int, l: int, avail: datetime, cid: int):
        # Insert into sorted list (small and stable; avoids extra imports).
        arr = buckets[(s, l)]
        arr.append((avail, cid))
        i = len(arr) - 1
        while i > 0 and arr[i - 1][0] > arr[i][0]:
            arr[i - 1], arr[i] = arr[i], arr[i - 1]
            i -= 1

    def bucket_peek(s: int, l: int):
        arr = buckets[(s, l)]
        return arr[0] if arr else None

    def bucket_pop(s: int, l: int):
        arr = buckets[(s, l)]
        return arr.pop(0) if arr else None

    for cid in cars_init:
        s0 = car_station[cid]
        l0 = car_level[cid]
        bucket_push(s0, l0, planning_start, cid)

    nearby = _sorted_nearby_stations(n_S, T)

    # Output containers
    assignment = [-1] * n_K
    relocation = []

    # Greedy order: prefer high-revenue orders first.
    # Objective = sum(accepted Rk) - 2*sum(rejected Rk) = constant + 3*sum(accepted Rk),
    # so maximizing accepted revenue is the key.
    order_ids = list(orders.keys())
    order_ids.sort(key=lambda oid: (-R[oid], orders[oid][3], oid))

    # How many alternative stations to scan when relocation is needed.
    # Keep small to stay fast on big instances.
    m_near = min(8, n_S)

    for oid in order_ids:
        req_lvl, ps, rs, pt, rt = orders[oid]
        deadline = pt - timedelta(minutes=30)
        if deadline < planning_start:
            deadline = planning_start

        best = None
        # Candidate tuples:
        # (extra_move_minutes, upgrade_flag(0 better), arrive_time, avail_time, from_station, car_id, car_level)

        # 1) Try cars already at pickup station (no relocation record)
        for lvl_opt in (req_lvl, req_lvl + 1):
            if lvl_opt > n_L:
                continue
            peek = bucket_peek(ps, lvl_opt)
            if peek is None:
                continue
            avail, cid = peek
            if avail <= deadline:
                best = (0, 0 if lvl_opt == req_lvl else 1, avail, avail, ps, cid, lvl_opt)
                break  # no-move feasible is always best on move minutes

        # 2) Try relocating from nearby stations (limited scan)
        if best is None and budget_left > 0:
            # Scan nearby stations in increasing travel time
            for from_s in nearby[ps][:m_near]:
                if from_s == ps:
                    continue
                move_minutes = T[(from_s, ps)]
                if move_minutes <= 0:
                    continue
                if move_minutes > budget_left:
                    continue

                for lvl_opt in (req_lvl, req_lvl + 1):
                    if lvl_opt > n_L:
                        continue
                    peek = bucket_peek(from_s, lvl_opt)
                    if peek is None:
                        continue
                    avail, cid = peek
                    arrive = avail + timedelta(minutes=move_minutes)
                    if arrive <= deadline:
                        cand = (
                            move_minutes,
                            0 if lvl_opt == req_lvl else 1,
                            arrive,
                            avail,
                            from_s,
                            cid,
                            lvl_opt,
                        )
                        if best is None:
                            best = cand
                        else:
                            # Primary: less moving time; Secondary: avoid upgrade; Tertiary: earlier arrival
                            if (cand[0], cand[1], cand[2]) < (best[0], best[1], best[2]):
                                best = cand

        if best is None:
            continue  # reject (assignment stays -1)

        move_minutes, _upgrade_flag, _arrive, avail, from_s, cid, lvl_used = best

        # Consume the chosen car from its current bucket
        popped = bucket_pop(from_s, lvl_used)
        if popped is None or popped[1] != cid:
            # Fallback: if the exact car isn't at head (due to ties), search and remove it.
            arr = buckets[(from_s, lvl_used)]
            found_idx = None
            for i, (_a, _cid) in enumerate(arr):
                if _cid == cid:
                    found_idx = i
                    break
            if found_idx is None:
                # Something inconsistent; safest is to reject this order
                continue
            arr.pop(found_idx)

        # If relocation needed, add a relocation record (start when car is ready/cleaned)
        if from_s != ps:
            relocation.append([int(cid), int(from_s), int(ps), _fmt_time(avail)])
            budget_left -= int(move_minutes)
            car_station[cid] = ps

        # Assign this order to this car
        assignment[oid - 1] = int(cid)

        # Update car state after serving the order:
        # Car is under control at rt+1h, then cleaned for 3h -> ready at rt+4h at return station.
        car_station[cid] = rs
        car_available[cid] = rt + timedelta(hours=4)

        bucket_push(rs, lvl_used, car_available[cid], cid)

    return assignment, relocation

