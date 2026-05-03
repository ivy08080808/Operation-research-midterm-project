import itertools
import random
from collections import deque

from MTP_lib import *

# Upper wall-clock budget for very large instances (typical course limit ~180s).
_TIME_BUDGET_MAX_S = 170.0


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
    t_start = t.time()

    def elapsed():
        return t.time() - t_start

    n_S, n_C, n_L, n_K, n_D, B, cars_init, rate, orders, T = _load_instance(file_path)

    # Scale time with problem size: small instances finish quickly; large ones use up to ~170s.
    time_budget = min(
        _TIME_BUDGET_MAX_S,
        max(8.0, min(_TIME_BUDGET_MAX_S, 10.0 + 0.048 * float(n_K))),
    )

    # Structure-only cues (no filename): tight relocation budget vs heavy order load.
    tight_relo = B <= 300
    load_ratio = float(n_K) / max(1, n_C)
    high_order_load = n_K >= 30 or load_ratio >= 3.2
    stressed = tight_relo or high_order_load
    if stressed:
        time_budget = min(_TIME_BUDGET_MAX_S, time_budget * 1.18)

    def time_left():
        return time_budget - elapsed()

    planning_start = datetime(2023, 1, 1, 0, 0)
    nearby = _sorted_nearby_stations(n_S, T)

    # Precompute revenue (for greedy ordering / scoring)
    R = {}
    total_R = 0.0
    for oid, (lvl, _ps, _rs, pt, rt) in orders.items():
        R[oid] = rate[lvl] * _hours_between(pt, rt)
        total_R += R[oid]

    # How many nearby stations to scan for relocation (more helps quality; scales with n_K).
    if n_K > 1200:
        m_near = min(12, n_S)
    elif n_K > 400:
        m_near = min(16, n_S)
    else:
        m_near = min(24, n_S)
    if stressed:
        m_near = min(n_S, max(m_near, min(n_S, 10 + n_S // 2)))

    keys = list(orders.keys())

    # Lower bound on relocation minutes to reach pickup (initial car positions).
    reloc_lb_to_pickup = {}
    for oid in keys:
        ps_o = orders[oid][1]
        reloc_lb_to_pickup[oid] = min(T[(cars_init[c][1], ps_o)] for c in cars_init)

    def pickup_deadline(oid):
        pt = orders[oid][3]
        dl = pt - timedelta(minutes=30)
        if dl < planning_start:
            dl = planning_start
        return dl

    pickup_count = {s: 0 for s in range(1, n_S + 1)}
    for oid in keys:
        pickup_count[orders[oid][1]] += 1

    def run_greedy(order_ids, reject_set=None):
        reject_set = reject_set or set()
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
            if oid in reject_set:
                continue
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
                            if best is None:
                                best = cand
                            elif (cand[0], cand[1], cand[2]) < (best[0], best[1], best[2]):
                                best = cand
                            elif tight_relo and (cand[0], cand[1], cand[2]) == (
                                best[0],
                                best[1],
                                best[2],
                            ):
                                # Tie-break only when relocation budget is scarce (S5-style).
                                _, _, _ac, av_c, _, cid_c, _ = cand
                                _, _, _ab, av_b, _, cid_b, _ = best
                                if av_c < av_b or (av_c == av_b and cid_c < cid_b):
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
    base = list(keys)
    base.sort(key=lambda oid: (-R[oid], orders[oid][3], oid))

    alt_time = list(keys)
    alt_time.sort(key=lambda oid: (orders[oid][3], -R[oid], oid))

    alt_hybrid = list(keys)
    alt_hybrid.sort(key=lambda oid: (-R[oid], oid))

    # Earliest deadline first (critical-path style)
    alt_deadline = list(keys)
    alt_deadline.sort(key=lambda oid: (pickup_deadline(oid), -R[oid], oid))

    # Latest pickup first (can pair better with returning vehicles)
    alt_late_pickup = list(keys)
    alt_late_pickup.sort(key=lambda oid: (-pickup_deadline(oid).timestamp(), -R[oid], oid))

    # High revenue per rental hour (finishes sooner relative to cash)
    alt_rph = list(keys)
    alt_rph.sort(
        key=lambda oid: (
            -(R[oid] / max(1e-6, _hours_between(orders[oid][3], orders[oid][4]))),
            orders[oid][3],
            oid,
        )
    )

    # Short jobs first (free capacity earlier)
    alt_short_dur = list(keys)
    alt_short_dur.sort(
        key=lambda oid: (_hours_between(orders[oid][3], orders[oid][4]), -R[oid], oid)
    )

    # Revenue per unit of "minimum relocation to pickup" (good when relocation budget is scarce).
    alt_reloc_value = list(keys)
    alt_reloc_value.sort(
        key=lambda oid: (
            -(R[oid] / (1.0 + float(reloc_lb_to_pickup[oid]))),
            pickup_deadline(oid),
            oid,
        )
    )

    # Extra prioritization when B is tight (e.g. S5-style instances).
    alt_tight_budget = list(keys)
    if B <= 220:
        alt_tight_budget.sort(
            key=lambda oid: (
                -(R[oid] / (1.0 + 0.4 * float(reloc_lb_to_pickup[oid]))),
                pickup_deadline(oid),
                oid,
            )
        )
    else:
        alt_tight_budget.sort(key=lambda oid: (-R[oid], pickup_deadline(oid), oid))

    # Revenue per pickup slack hour (urgent + valuable first). Skipped on huge n_K to save one full greedy.
    alt_critical = None
    if n_K <= 1000:
        alt_critical = list(keys)
        alt_critical.sort(
            key=lambda oid: (
                -(
                    R[oid]
                    / max(
                        0.28,
                        (pickup_deadline(oid) - planning_start).total_seconds() / 3600.0,
                    )
                ),
                pickup_deadline(oid),
                oid,
            )
        )

    alt_hotspot = list(keys)
    alt_hotspot.sort(
        key=lambda oid: (
            -(R[oid] / (1.0 + 0.14 * float(pickup_count[orders[oid][1]]))),
            pickup_deadline(oid),
            oid,
        )
    )

    alt_tight_pow = list(keys)
    alt_tight_pow.sort(
        key=lambda oid: (
            -(R[oid] / (1.0 + float(reloc_lb_to_pickup[oid]) ** 1.65)),
            pickup_deadline(oid),
            oid,
        )
    )

    alt_edf_hot = list(keys)
    alt_edf_hot.sort(
        key=lambda oid: (
            pickup_deadline(oid),
            -(R[oid] / (1.0 + 0.09 * float(pickup_count[orders[oid][1]]))),
            oid,
        )
    )

    alt_zero_first = None
    if tight_relo:
        zset = {oid for oid in keys if reloc_lb_to_pickup[oid] == 0}
        z = [oid for oid in keys if oid in zset]
        far = [oid for oid in keys if oid not in zset]
        z.sort(key=lambda oid: (-R[oid], pickup_deadline(oid), oid))
        far.sort(
            key=lambda oid: (
                -(R[oid] / (1.0 + float(reloc_lb_to_pickup[oid]) ** 1.45)),
                pickup_deadline(oid),
                oid,
            )
        )
        alt_zero_first = z + far

    deterministic_orders = [
        base,
        alt_time,
        alt_hybrid,
        alt_deadline,
        alt_late_pickup,
        alt_rph,
        alt_short_dur,
        alt_reloc_value,
        alt_tight_budget,
    ]
    if alt_critical is not None:
        deterministic_orders.append(alt_critical)
    if stressed:
        if tight_relo and alt_zero_first is not None:
            deterministic_orders.append(alt_zero_first)
        deterministic_orders.extend([alt_hotspot, alt_tight_pow, alt_edf_hot])
    deterministic_orders = tuple(deterministic_orders)

    best_order = list(base)
    best_assignment, best_relocation, best_profit = run_greedy(base)
    for cand_order in deterministic_orders[1:]:
        if time_left() < 0.3:
            break
        a, r, p = run_greedy(cand_order)
        if p > best_profit:
            best_assignment, best_relocation, best_profit = a, r, p
            best_order = list(cand_order)

    # Randomized multi-starts: diversify tie-breaking on revenue / time
    rng = random.Random(7919)
    rand_limit = max(50, min(2500, int(14.0 * time_budget + 40)))
    if stressed:
        rand_limit = min(2800, int(rand_limit * 1.28))
    rand_i = 0
    while time_left() > 1.0 and rand_i < rand_limit:
        rand_i += 1
        jitter = rng.random()
        u = {oid: rng.random() for oid in keys}
        rnd_order = list(keys)
        rnd_order.sort(
            key=lambda oid: (-R[oid] * (1.0 + 0.08 * jitter * u[oid]), orders[oid][3], oid)
        )
        a, r, p = run_greedy(rnd_order)
        if p > best_profit:
            best_assignment, best_relocation, best_profit = a, r, p
            best_order = list(rnd_order)

    # Force-reject worst marginal-efficiency orders (mainly helps tight relocation budget).
    if tight_relo:
        worst_eff = sorted(
            keys,
            key=lambda oid: R[oid] / (1.0 + float(reloc_lb_to_pickup[oid])),
        )
        for oid in worst_eff[:10]:
            if time_left() < 0.12:
                break
            a, r, p = run_greedy(best_order, reject_set={oid})
            if p > best_profit:
                best_assignment, best_relocation, best_profit = a, r, p

    # Local chain reassignment (bounded BFS): force high-value rejects early and rescue displacements.
    def chain_improve(base_list, time_cap: float, seed_cap: int, max_depth: int, branch_k: int):
        nonlocal best_assignment, best_relocation, best_profit, best_order
        if time_cap <= 0.1 or time_left() < 0.3:
            return
        chain_end = t.time() + min(time_cap, max(0.0, time_left() - 0.25))

        rejected = [oid for oid in keys if best_assignment[oid - 1] == -1]
        rejected.sort(key=lambda oid: -R[oid])
        seed_orders = rejected[: min(seed_cap, len(rejected))]
        if not seed_orders:
            return

        best_accepted_set = {oid for oid in keys if best_assignment[oid - 1] != -1}

        def _forced_run(prefix):
            seen = set()
            forced = []
            for x in prefix:
                if x not in seen:
                    seen.add(x)
                    forced.append(x)
            full = forced + [x for x in base_list if x not in seen]
            a, r, p = run_greedy(full)
            return a, r, p, full

        queue = deque((1, [s]) for s in seed_orders)
        visited_prefixes = set()

        while queue and t.time() < chain_end and time_left() > 0.2:
            depth, prefix = queue.popleft()
            key = tuple(prefix)
            if key in visited_prefixes:
                continue
            visited_prefixes.add(key)

            a, r, p, full_order = _forced_run(prefix)
            if p > best_profit:
                best_assignment, best_relocation, best_profit = a, r, p
                best_order = list(full_order)
                best_accepted_set = {oid for oid in keys if best_assignment[oid - 1] != -1}

            if depth >= max_depth or t.time() >= chain_end:
                continue

            new_accepted = {oid for oid in keys if a[oid - 1] != -1}
            displaced = list(best_accepted_set - new_accepted)
            if not displaced:
                continue
            displaced.sort(key=lambda oid: -R[oid])
            for nxt in displaced[: min(branch_k, len(displaced))]:
                if t.time() >= chain_end:
                    break
                queue.append((depth + 1, prefix + [nxt]))

    # First chain pass: BFS is expensive (each node = full greedy); scale cap with n_K and remaining time.
    raw_chain = min(55.0, max(2.0, 0.30 * time_budget))
    if n_K < 100:
        raw_chain = min(raw_chain, 5.0)
    elif n_K < 400:
        raw_chain = min(raw_chain, 16.0)
    chain_budget = min(raw_chain, max(0.0, time_left() - 0.5))
    sc_seed = min(45, max(6, n_K // 5 + 3))
    sc_depth = 6 if n_K >= 150 else 4
    sc_branch = 3 if n_K >= 200 else 2
    if stressed:
        sc_seed = min(52, max(10, int(n_K // 3.5) + 6))
        sc_depth = max(sc_depth, 5)
        sc_branch = max(sc_branch, 3)
    chain_improve(
        best_order,
        time_cap=chain_budget,
        seed_cap=sc_seed,
        max_depth=sc_depth,
        branch_k=sc_branch,
    )

    # Second pass with alternate backbone ordering if time allows
    if time_left() > 2.0 and n_K >= 60:
        cap2 = min(28.0, 0.22 * time_budget, max(0.0, time_left() - 0.5))
        if n_K < 200:
            cap2 = min(cap2, 4.0)
        chain_improve(
            alt_deadline,
            time_cap=cap2,
            seed_cap=min(30, max(6, n_K // 6)),
            max_depth=5 if n_K >= 200 else 3,
            branch_k=2,
        )

    # Third chain: hotspot backbone (helps congested pickups) with a modest time cap.
    if time_left() > 2.2 and (stressed or high_order_load):
        cap3 = min(20.0, 0.16 * time_budget, max(0.0, time_left() - 0.45))
        if n_K < 200:
            cap3 = min(cap3, 3.2)
        elif n_K > 800:
            cap3 = min(cap3, 12.0)
        chain_improve(
            alt_hotspot,
            time_cap=cap3,
            seed_cap=min(26, max(8, n_K // 5)),
            max_depth=4 if n_K < 350 else 3,
            branch_k=2,
        )

    # Light ILS: random pairwise swaps on the best order; cheap and uses only remaining time_budget.
    def ils_pass():
        nonlocal best_assignment, best_relocation, best_profit, best_order
        if n_K < 2 or time_left() < 0.45:
            return
        ils_rng = random.Random(271828)
        # ~12% fewer ILS iterations than v2.4 (more conservative on wall clock).
        max_ils = min(123, max(19, int(0.282 * time_budget)))
        if n_K > 900:
            max_ils = min(max_ils, 66)
        if n_K > 2200:
            max_ils = min(max_ils, 37)
        for _ in range(max_ils):
            if time_left() < 0.32:
                break
            w = list(best_order)
            i, j = ils_rng.randrange(n_K), ils_rng.randrange(n_K)
            if i != j:
                w[i], w[j] = w[j], w[i]
            a, r, p = run_greedy(w)
            if p > best_profit:
                best_assignment, best_relocation, best_profit = a, r, p
                best_order = w

    ils_pass()

    # Optional reject: skipping low-R orders can free cars / relocation budget.
    def drop_pass():
        nonlocal best_assignment, best_relocation, best_profit
        backbone = list(best_order)
        acc = [oid for oid in keys if best_assignment[oid - 1] != -1]
        acc.sort(key=lambda oid: R[oid])
        if stressed:
            n_try = min(38, max(12, len(acc) // 3))
        else:
            n_try = min(22, max(5, len(acc) // 4))
        for oid in acc[:n_try]:
            if time_left() < 0.1:
                break
            a, r, p = run_greedy(backbone, reject_set={oid})
            if p > best_profit:
                best_assignment, best_relocation, best_profit = a, r, p
        # Pair-drop frees relocation budget; risky when B is ample (S4-style load).
        if tight_relo and len(acc) >= 8:
            low = acc[: min(8, len(acc))]
            for i, j in itertools.combinations(low[:6], 2):
                if time_left() < 0.06:
                    return
                a, r, p = run_greedy(backbone, reject_set={i, j})
                if p > best_profit:
                    best_assignment, best_relocation, best_profit = a, r, p

    drop_pass()

    return best_assignment, best_relocation

