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
    nearby = _sorted_nearby_stations(n_S, T)

    # Precompute revenue (for greedy ordering / scoring)
    R = {}
    total_R = 0.0
    for oid, (lvl, _ps, _rs, pt, rt) in orders.items():
        R[oid] = rate[lvl] * _hours_between(pt, rt)
        total_R += R[oid]

    # How many alternative stations to scan when relocation is needed.
    m_near = min(8, n_S)

    def run_greedy(order_ids):
        # Car state (dynamic)
        car_level = {cid: cars_init[cid][0] for cid in cars_init}
        car_station = {cid: cars_init[cid][1] for cid in cars_init}
        car_available = {cid: planning_start for cid in cars_init}  # ready time at current station

        # Remaining relocation budget (minutes)
        budget_left = int(B)

        # Buckets by (station, level): each value is a list of (available_time, car_id) sorted ascending.
        buckets = {(s, l): [] for s in range(1, n_S + 1) for l in range(1, n_L + 1)}

        def bucket_push(s: int, l: int, avail: datetime, cid: int):
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
            bucket_push(car_station[cid], car_level[cid], planning_start, cid)

        assignment = [-1] * n_K
        relocation = []
        accepted_sum = 0.0

        for oid in order_ids:
            req_lvl, ps, rs, pt, rt = orders[oid]
            deadline = pt - timedelta(minutes=30)
            if deadline < planning_start:
                deadline = planning_start

            best = None
            # (move_minutes, upgrade_flag, arrive_time, avail_time, from_station, car_id, car_level)

            # Same-station cars first
            for lvl_opt in (req_lvl, req_lvl + 1):
                if lvl_opt > n_L:
                    continue
                peek = bucket_peek(ps, lvl_opt)
                if peek is None:
                    continue
                avail, cid = peek
                if avail <= deadline:
                    best = (0, 0 if lvl_opt == req_lvl else 1, avail, avail, ps, cid, lvl_opt)
                    break

            # Relocate from nearby stations
            if best is None and budget_left > 0:
                for from_s in nearby[ps][:m_near]:
                    if from_s == ps:
                        continue
                    move_minutes = T[(from_s, ps)]
                    if move_minutes <= 0 or move_minutes > budget_left:
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
                            if best is None or (cand[0], cand[1], cand[2]) < (best[0], best[1], best[2]):
                                best = cand

            if best is None:
                continue

            move_minutes, _upgrade_flag, _arrive, avail, from_s, cid, lvl_used = best

            popped = bucket_pop(from_s, lvl_used)
            if popped is None or popped[1] != cid:
                arr = buckets[(from_s, lvl_used)]
                found_idx = None
                for i, (_a, _cid) in enumerate(arr):
                    if _cid == cid:
                        found_idx = i
                        break
                if found_idx is None:
                    continue
                arr.pop(found_idx)

            if from_s != ps:
                relocation.append([int(cid), int(from_s), int(ps), _fmt_time(avail)])
                budget_left -= int(move_minutes)
                car_station[cid] = ps

            assignment[oid - 1] = int(cid)
            accepted_sum += R[oid]

            car_station[cid] = rs
            car_available[cid] = rt + timedelta(hours=4)
            bucket_push(rs, lvl_used, car_available[cid], cid)

        profit = -2.0 * total_R + 3.0 * accepted_sum
        return assignment, relocation, profit

    # Base ordering strategies
    base = list(orders.keys())
    base.sort(key=lambda oid: (-R[oid], orders[oid][3], oid))

    alt_time = list(orders.keys())
    alt_time.sort(key=lambda oid: (orders[oid][3], -R[oid], oid))

    # Hybrid: high revenue, but prefer earlier pickup when revenues tie
    alt_hybrid = list(orders.keys())
    alt_hybrid.sort(key=lambda oid: (-R[oid], oid))

    # Run a few starts, keep best
    best_assignment, best_relocation, best_profit = run_greedy(base)
    for cand_order in (alt_time, alt_hybrid):
        a, r, p = run_greedy(cand_order)
        if p > best_profit:
            best_assignment, best_relocation, best_profit = a, r, p

    # Chain reallocation idea:
    # Focus on high-value rejected orders, force them early and rerun greedy.
    # This triggers global reassignment chains cheaply (multi-start with priority injection).
    rejected = [oid for oid in orders.keys() if best_assignment[oid - 1] == -1]
    rejected.sort(key=lambda oid: -R[oid])
    topM = rejected[: min(10, len(rejected))]

    start_t = t.time()
    for oid_force in topM:
        if t.time() - start_t > 2.0:  # keep this extra loop lightweight
            break
        forced_order = [oid_force] + [x for x in base if x != oid_force]
        a, r, p = run_greedy(forced_order)
        if p > best_profit:
            best_assignment, best_relocation, best_profit = a, r, p

    return best_assignment, best_relocation

