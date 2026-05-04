import itertools
from collections import deque

from MTP_lib import *

# Wall-clock ceiling for the heuristic only (course / judge may impose a separate cap).
_TIME_BUDGET_MAX_S = 180.0


def _problem_scale(n_K: int, n_C: int, n_S: int) -> float:
    """
    Dimensionless scale >= 1.0 so caps grow with instance size instead of fixed n_K cutoffs.
    Sublinear in n_K so very large synthetic cases stay tractable.
    """
    return max(
        1.0,
        0.22 * (float(n_K) / 40.0) ** 0.58 + 0.10 * float(n_C) / max(1.0, float(n_S)),
    )


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

    ps = _problem_scale(n_K, n_C, n_S)
    # Heuristic time grows with instance; cap keeps runaway cases bounded (Gurobi time is separate).
    time_budget = min(
        _TIME_BUDGET_MAX_S,
        max(14.0, min(_TIME_BUDGET_MAX_S, 11.5 + 1.05 * float(n_K) * ps)),
    )

    # Structure-only cues (no filename): tight relocation budget vs heavy order load.
    tight_relo = B <= 300
    load_ratio = float(n_K) / max(1, n_C)
    high_order_load = n_K >= 30 or load_ratio >= 3.2
    stressed = tight_relo or high_order_load
    if stressed:
        time_budget = min(_TIME_BUDGET_MAX_S, time_budget * 1.18)
    if tight_relo:
        time_budget = min(_TIME_BUDGET_MAX_S, time_budget * 1.22)

    # Generic size caps (replace magic n_K thresholds like 66 / 900 / 1000).
    insertion_skip_n = max(
        820, min(1750, int(250 + 11.5 * float(n_C) + 14.0 * (float(n_K) ** 0.68)))
    )
    ils_cap_n = max(650, min(6000, int(480 + 1.1 * float(n_K) * ps)))
    chain_large_n = max(90, min(900, int(36 + 0.85 * float(n_K) ** 0.82)))
    branch_k_large_threshold = max(72, min(520, int(24 + 2.8 * float(n_C))))
    ins_pos_budget = min(320, max(22, int(16 + 0.42 * float(n_K) + 0.9 * float(n_C))))

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
    # Tight relocation budget (S5): scan more nearby stations — still O(n_K * m_near * k_reloc).
    if tight_relo:
        m_near = min(n_S, m_near + 10)

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

    # Lagrangian-style pickup-station penalty (mutated during λ-sweep); 0 = off.
    lag_pickup_weight = [0.0]

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

        def _cand_sort_key(move_minutes, upgrade_flag, arrive_dt, avail_dt, car_id, oid_for_r):
            mv = float(move_minutes)
            # Tight B: penalize burning relocation minutes in the lexicographic key (reduces S5 variance).
            if tight_relo and mv > 0.0:
                mv *= 1.0 + 1.85 * mv / max(1.0, float(B))
            # Slightly superlinear move penalty when B is tight: keep polynomial, favor high-R serves.
            if tight_relo:
                eff = R[oid_for_r] / (1.0 + float(move_minutes) ** 1.14)
            else:
                eff = R[oid_for_r] / (1.0 + float(move_minutes))
            ps_k = orders[oid_for_r][1]
            eff -= lag_pickup_weight[0] * float(pickup_count[ps_k])
            return (
                mv,
                upgrade_flag,
                arrive_dt,
                -eff,
                avail_dt,
                car_id,
            )

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

            # Same-station: scan more bucket entries when B is tight (S5) or stressed.
            k_same = 10 if tight_relo else (4 if stressed else 1)
            for lvl_opt in (req_lvl, req_lvl + 1):
                if lvl_opt > n_L:
                    continue
                arr = buckets[(ps, lvl_opt)]
                for peek in arr[:k_same]:
                    avail, cid = peek
                    if avail > deadline:
                        break
                    cand = (0, 0 if lvl_opt == req_lvl else 1, avail, avail, ps, cid, lvl_opt)
                    if best is None:
                        best = cand
                    else:
                        kc = _cand_sort_key(cand[0], cand[1], cand[2], cand[3], cand[5], oid)
                        kb = _cand_sort_key(best[0], best[1], best[2], best[3], best[5], oid)
                        if kc < kb:
                            best = cand
                # Under tight B, still consider req_lvl+1 same-station cars (upgrade) before fixing best.
                if best is not None and best[0] == 0 and best[4] == ps and (not tight_relo):
                    break

            # Relocate from nearby stations (top-k per bucket when stressed / large instances).
            k_reloc = 5 if (stressed and n_K >= 40) else 3 if stressed else 1
            if tight_relo:
                k_reloc = max(k_reloc, 8 if n_K >= 36 else 7)
            if n_K >= 120:
                k_reloc = max(k_reloc, 4)
            if budget_left > 0 and (best is None or tight_relo):
                for from_s in nearby[ps][:m_near]:
                    if from_s == ps:
                        continue
                    move_minutes = T[(from_s, ps)]
                    if move_minutes <= 0 or move_minutes > budget_left:
                        continue
                    for lvl_opt in (req_lvl, req_lvl + 1):
                        if lvl_opt > n_L:
                            continue
                        arr = buckets[(from_s, lvl_opt)]
                        for peek in arr[:k_reloc]:
                            avail, cid = peek
                            arrive = avail + timedelta(minutes=move_minutes)
                            if arrive > deadline:
                                continue
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
                                kc = _cand_sort_key(cand[0], cand[1], cand[2], cand[3], cand[5], oid)
                                kb = _cand_sort_key(best[0], best[1], best[2], best[3], best[5], oid)
                                if kc < kb:
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

    # Revenue per pickup slack hour (urgent + valuable first). Cost is one extra greedy; scale cutoff with size.
    alt_critical = None
    if n_K <= min(12000, int(820 + 260.0 * ps + 4.5 * float(n_C))):
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

    # Softer reloc penalty + deadline (helps some S5 where 1.65 power over-prioritizes tiny-LB orders).
    alt_tight_balanced = list(keys)
    alt_tight_balanced.sort(
        key=lambda oid: (
            -(R[oid] / (1.0 + float(reloc_lb_to_pickup[oid]) ** 1.22)),
            pickup_deadline(oid),
            -R[oid],
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

    # Tight pickup slack then revenue (S4 crowded horizon / contention).
    alt_slack_then_R = list(keys)
    alt_slack_then_R.sort(
        key=lambda oid: (
            (pickup_deadline(oid) - planning_start).total_seconds(),
            -R[oid],
            orders[oid][3],
            oid,
        )
    )

    # Cheap orders that tie up cars a long time — try them after high marginal value (S4).
    alt_long_job_last = list(keys)
    alt_long_job_last.sort(
        key=lambda oid: (
            -(
                R[oid]
                / max(
                    0.35,
                    _hours_between(orders[oid][3], orders[oid][4]),
                )
            ),
            pickup_deadline(oid),
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

    # --- Numpy-backed relocation proxy + construction orders (no solver; PDF-allowed libs) ---
    oid_rank = sorted(keys)
    n_ci = len(oid_rank)
    idx_map = {oid: i for i, oid in enumerate(oid_rank)}
    rs_ps_np = None
    reloc_vec = None
    if 2 <= n_ci <= 140:
        rs_ps_np = np.empty((n_ci, n_ci), dtype=np.int64)
        reloc_vec = np.array([float(reloc_lb_to_pickup[o]) for o in oid_rank], dtype=np.float64)
        for ii in range(n_ci):
            rsi = orders[oid_rank[ii]][2]
            row = rs_ps_np[ii]
            for jj in range(n_ci):
                row[jj] = int(T[(rsi, orders[oid_rank[jj]][1])])

    def chain_proxy_oids(seq):
        if not seq:
            return 0.0
        if rs_ps_np is not None and reloc_vec is not None:
            idxs = [idx_map[o] for o in seq]
            s = float(reloc_vec[idxs[0]])
            for a in range(len(idxs) - 1):
                s += float(rs_ps_np[idxs[a], idxs[a + 1]])
            return s
        o0 = seq[0]
        s = float(reloc_lb_to_pickup[o0])
        for a in range(len(seq) - 1):
            s += float(T[(orders[seq[a]][2], orders[seq[a + 1]][1])])
        return s

    def build_cheapest_insertion_order():
        if n_ci < 2 or n_ci > 140 or n_ci**3 > 2_800_000:
            return None
        rem = set(oid_rank)
        first = max(oid_rank, key=lambda o: (R[o], -orders[o][3].timestamp()))
        rem.remove(first)
        ord_li = [first]
        while rem:
            best_key = None
            best_trip = None
            best_oid = None
            for oid in rem:
                L = len(ord_li)
                for pos in range(L + 1):
                    cand = ord_li[:pos] + [oid] + ord_li[pos:]
                    pc = chain_proxy_oids(cand)
                    key = (pc, -R[oid], oid)
                    if best_key is None or key < best_key:
                        best_key = key
                        best_trip = cand
                        best_oid = oid
            ord_li = best_trip
            rem.remove(best_oid)
        return ord_li

    def build_return_revenue_chain_order():
        """Greedy spatial chain: from last return, bias next pickup by R / (1+reloc_minutes)."""
        rem = set(keys)
        cur = max(keys, key=lambda o: (R[o], -orders[o][3].timestamp()))
        out = [cur]
        rem.remove(cur)
        while rem:
            rs = orders[cur][2]
            cur = max(
                rem,
                key=lambda j: (
                    R[j] / (1.0 + float(T[(rs, orders[j][1])])),
                    -orders[j][3].timestamp(),
                    -R[j],
                ),
            )
            out.append(cur)
            rem.remove(cur)
        return out

    rr_chain_order = build_return_revenue_chain_order()
    extra_ctor_orders = [rr_chain_order]

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
    if tight_relo:
        # Reverse EDF / time backbones help when freeing relocation early is misleading (S5-style).
        deterministic_orders.append(list(reversed(alt_deadline)))
        deterministic_orders.append(list(reversed(alt_time)))
    if alt_critical is not None:
        deterministic_orders.append(alt_critical)
    if stressed:
        if tight_relo and alt_zero_first is not None:
            deterministic_orders.append(alt_zero_first)
        deterministic_orders.extend([alt_hotspot, alt_tight_pow, alt_edf_hot])
        if tight_relo:
            deterministic_orders.append(alt_tight_balanced)
        # S4-style crowding only; extra time orderings can scramble S5 (tight B, fewer orders).
        if high_order_load:
            deterministic_orders.extend([alt_slack_then_R, alt_long_job_last])
    deterministic_orders = tuple(deterministic_orders)

    best_order = list(base)
    best_assignment, best_relocation, best_profit = run_greedy(base)
    for cand_order in extra_ctor_orders + list(deterministic_orders)[1:]:
        if time_left() < 0.3:
            break
        a, r, p = run_greedy(cand_order)
        if p > best_profit:
            best_assignment, best_relocation, best_profit = a, r, p
            best_order = list(cand_order)

    # Cheapest-insertion backbone is O(n^4): run only after core orderings so time_left stays healthy.
    if 2 <= n_ci <= 140 and n_ci**3 <= 2_800_000 and time_left() > 8.0:
        c_ins = build_cheapest_insertion_order()
        if c_ins is not None and time_left() > 0.45:
            a, r, p = run_greedy(c_ins)
            if p > best_profit:
                best_assignment, best_relocation, best_profit = a, r, p
                best_order = list(c_ins)

    # Randomized multi-starts: diversify tie-breaking on revenue / time (numpy RNG only).
    rng = np.random.default_rng(7919)
    rand_limit = max(72, min(12000, int(20.0 * time_budget * ps + 80)))
    if stressed:
        rand_limit = min(14000, int(rand_limit * 1.28))
    if tight_relo and n_K < 95:
        rand_limit = min(16000, int(rand_limit * 1.18))
    rand_i = 0
    while time_left() > 1.0 and rand_i < rand_limit:
        rand_i += 1
        jitter = float(rng.random())
        _ks = sorted(keys)
        u_arr = rng.random(size=len(_ks))
        u = {oid: float(u_arr[i]) for i, oid in enumerate(_ks)}
        rnd_order = list(keys)
        rnd_order.sort(
            key=lambda oid: (-R[oid] * (1.0 + 0.08 * jitter * u[oid]), orders[oid][3], oid)
        )
        a, r, p = run_greedy(rnd_order)
        if p > best_profit:
            best_assignment, best_relocation, best_profit = a, r, p
            best_order = list(rnd_order)

    # Force-reject worst marginal-efficiency orders (S5: tight B; S4: free fleet for better packing).
    if tight_relo:
        worst_eff = sorted(
            keys,
            key=lambda oid: R[oid] / (1.0 + float(reloc_lb_to_pickup[oid])),
        )
        for oid in worst_eff[:38]:
            if time_left() < 0.12:
                break
            a, r, p = run_greedy(best_order, reject_set={oid})
            if p > best_profit:
                best_assignment, best_relocation, best_profit = a, r, p
        # Pairs of "cheap per LB-minute" orders (bounded count; polynomial).
        for i, j in itertools.combinations(worst_eff[:10], 2):
            if time_left() < 0.08:
                break
            a, r, p = run_greedy(best_order, reject_set={i, j})
            if p > best_profit:
                best_assignment, best_relocation, best_profit = a, r, p
    elif high_order_load:
        worst_eff_h = sorted(
            keys,
            key=lambda oid: R[oid] / (1.0 + float(reloc_lb_to_pickup[oid])),
        )
        for oid in worst_eff_h[:20]:
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
    raw_chain = min(68.0, max(2.0, 0.38 * time_budget))
    if n_K < 100:
        raw_chain = min(raw_chain, 14.0 if high_order_load else 8.5)
    elif n_K < int(chain_large_n):
        raw_chain = min(raw_chain, 22.0 if high_order_load else 20.0)
    elif n_K < 1600:
        raw_chain = min(raw_chain, 28.0 if high_order_load else 24.0)
    chain_budget = min(raw_chain, max(0.0, time_left() - 0.5))
    if high_order_load:
        chain_budget = min(_TIME_BUDGET_MAX_S, chain_budget * 1.14)
    sc_seed = min(56, max(8, n_K // 4 + 4))
    sc_depth = 6 if n_K >= 150 else 4
    sc_branch = 3 if n_K >= 200 else 2
    if high_order_load:
        sc_seed = min(58, max(12, int(n_K // 3.2) + 8))
        sc_depth = max(sc_depth, 5 if n_K >= 28 else 4)
        sc_branch = max(sc_branch, 3 if n_K >= 18 else 2)
    elif tight_relo:
        sc_seed = min(52, max(10, int(n_K // 3.5) + 6))
        sc_depth = max(sc_depth, 5)
        sc_branch = max(sc_branch, 3)
    # S5-style: tight B with moderate n_K (often also high_order_load — elif above skipped).
    if tight_relo and 40 <= n_K < 95:
        sc_seed = min(60, max(sc_seed, max(14, int(n_K // 3.0) + 8)))
        sc_depth = max(sc_depth, 6)
        sc_branch = max(sc_branch, 4)
        chain_budget = min(_TIME_BUDGET_MAX_S, chain_budget * 1.12)
    chain_improve(
        best_order,
        time_cap=chain_budget,
        seed_cap=sc_seed,
        max_depth=sc_depth,
        branch_k=sc_branch,
    )

    # Second chain on deadline backbone: threshold scales with fleet (not fixed n_K>=60).
    second_chain_min_n = max(40, min(78, int(22 + 0.95 * float(n_C))))
    if time_left() > 2.0 and n_K >= second_chain_min_n:
        cap2 = min(34.0, 0.28 * time_budget, max(0.0, time_left() - 0.5))
        if n_K < int(chain_large_n):
            cap2 = min(cap2, 6.5 + 0.012 * float(n_K))
        else:
            cap2 = min(cap2, 11.0)
        chain_improve(
            alt_deadline,
            time_cap=cap2,
            seed_cap=min(48, max(6, int(n_K // max(4.0, 5.5 - 0.02 * float(n_C))))),
            max_depth=5 if n_K >= int(0.55 * chain_large_n) else 3,
            branch_k=2,
        )
    elif time_left() > 1.15 and tight_relo and n_K < 60:
        cap2s = min(18.0, 0.36 * time_budget, max(0.0, time_left() - 0.45))
        chain_improve(
            alt_deadline,
            time_cap=cap2s,
            seed_cap=min(26, max(10, n_K + n_K // 2)),
            max_depth=6,
            branch_k=3,
        )

    # Third chain: hotspot backbone (helps congested pickups) with a modest time cap.
    if time_left() > 2.2 and (stressed or high_order_load):
        cap3 = min(26.0, 0.21 * time_budget, max(0.0, time_left() - 0.45))
        if n_K < int(chain_large_n):
            cap3 = min(cap3, 7.5 if high_order_load else 5.0)
        elif n_K > 800:
            cap3 = min(cap3, 14.0)
        chain_improve(
            alt_hotspot,
            time_cap=cap3,
            seed_cap=min(48, max(10, int(n_K // max(3.5, 4.2 - 0.018 * float(n_C))))),
            max_depth=4 if n_K < int(0.9 * chain_large_n) else 3,
            branch_k=3 if high_order_load and n_K < branch_k_large_threshold else 2,
        )

    if time_left() > 1.0 and stressed and high_order_load:
        cap4 = min(18.0, 0.22 * time_budget, max(0.0, time_left() - 0.4))
        chain_improve(
            alt_slack_then_R,
            time_cap=cap4,
            seed_cap=min(24, max(10, n_K // 4)),
            max_depth=4,
            branch_k=2,
        )

    if time_left() > 1.35 and tight_relo and 36 <= n_K < 95:
        cap_tb = min(16.0, 0.22 * time_budget, max(0.0, time_left() - 0.38))
        chain_improve(
            alt_tight_balanced,
            time_cap=cap_tb,
            seed_cap=min(40, max(12, n_K // 3)),
            max_depth=5,
            branch_k=3,
        )

    # Light ILS: random pairwise swaps on the best order; cheap and uses only remaining time_budget.
    def ils_pass():
        nonlocal best_assignment, best_relocation, best_profit, best_order
        if n_K < 2 or time_left() < 0.45:
            return
        ils_rng = np.random.default_rng(271828)
        max_ils = min(260, max(28, int(0.44 * time_budget * ps)))
        if high_order_load:
            max_ils = min(320, int(max_ils * 1.26))
        max_ils = min(max_ils, max(40, int(2400.0 / max(ps, 0.35) ** 0.55)))
        max_ils = min(max_ils, max(32, int(7200.0 / max(n_K, 1) ** 0.42)))
        max_ils = min(max_ils, ils_cap_n)
        if tight_relo and n_K < 95:
            max_ils = min(int(ils_cap_n * 1.15), int(max_ils * 1.24))
        for _ in range(max_ils):
            if time_left() < 0.32:
                break
            w = list(best_order)
            i, j = int(ils_rng.integers(0, n_K)), int(ils_rng.integers(0, n_K))
            if i != j:
                w[i], w[j] = w[j], w[i]
            a, r, p = run_greedy(w)
            if p > best_profit:
                best_assignment, best_relocation, best_profit = a, r, p
                best_order = w

    ils_pass()

    # Early bounded prefix search while time_left is still healthy (moved before heavy insertion/drop).
    if tight_relo and n_K <= 62 and time_left() > 1.2:
        top7 = sorted(keys, key=lambda o: -R[o])[:7]
        pfx_end = t.time() + min(18.0, max(0.0, time_left() - 0.55))
        for a, b, c in itertools.permutations(top7, 3):
            if t.time() > pfx_end or time_left() < 0.35:
                break
            seen3 = {a, b, c}
            od = [a, b, c] + [x for x in alt_deadline if x not in seen3]
            a2, r2, p2 = run_greedy(od)
            if p2 > best_profit:
                best_assignment, best_relocation, best_profit = a2, r2, p2
                best_order = list(od)

    def insertion_improve():
        """Try moving a high-value rejected order earlier in the processing sequence."""
        nonlocal best_assignment, best_relocation, best_profit, best_order
        if n_K < 4:
            return
        if n_K > insertion_skip_n:
            return
        t_end = t.time() + min(
            min(72.0 if tight_relo else 55.0, (0.40 if tight_relo else 0.32) * time_budget * ps),
            max(5.5 if tight_relo else 4.0, (0.44 if tight_relo else 0.36) * time_budget),
            max(0.0, time_left() - 0.45),
        )
        rejected = [oid for oid in keys if best_assignment[oid - 1] == -1]
        if not rejected:
            return
        rejected.sort(key=lambda oid: -R[oid])
        max_rej_try = min(
            len(rejected),
            max(10, int(10 + 0.38 * float(n_C) + 0.22 * float(n_K) ** 0.5)),
            68 if tight_relo else 44,
        )
        for oid in rejected[:max_rej_try]:
            if t.time() > t_end or time_left() < 0.28:
                break
            base_wo = [x for x in best_order if x != oid]
            n = len(base_wo)
            if n == 0:
                continue
            if n <= 22:
                pos_iter = range(n + 1)
            else:
                pos_set = {
                    0,
                    1,
                    2,
                    n // 6,
                    n // 4,
                    n // 3,
                    n // 2,
                    (2 * n) // 3,
                    (3 * n) // 4,
                    (5 * n) // 6,
                    n - 4,
                    n - 3,
                    n - 2,
                    n - 1,
                    n,
                }
                if high_order_load or n_K >= 48:
                    step = max(1, n // max(6, int(18 * ps)))
                    for q in range(0, n + 1, step):
                        pos_set.add(min(n, q))
                    for frac in (5, 7, 9, 11, 13, 15, 17, 19):
                        if n > 0:
                            pos_set.add(min(n, (n * frac) // 24))
                pos_iter = sorted(pos_set)
                if len(pos_iter) > ins_pos_budget:
                    stride = max(1, len(pos_iter) // ins_pos_budget)
                    pos_iter = pos_iter[::stride] + [pos_iter[-1]]
                    pos_iter = sorted(set(pos_iter))
            for pos in pos_iter:
                if pos > n:
                    continue
                new_o = base_wo[:pos] + [oid] + base_wo[pos:]
                a, r, p = run_greedy(new_o)
                if p > best_profit:
                    best_assignment, best_relocation, best_profit = a, r, p
                    best_order = new_o

    def adjacent_sweep():
        """Greedy is order-dependent; single adjacent swaps can unblock high-R orders."""
        nonlocal best_assignment, best_relocation, best_profit, best_order
        if n_K < 3 or time_left() < 0.45:
            return
        t_end = t.time() + min(
            40.0 if tight_relo else (32.0 if high_order_load else 24.0),
            max(
                4.2 if tight_relo else (3.0 if high_order_load else 2.4),
                (0.36 if tight_relo else (0.30 if high_order_load else 0.24)) * time_budget,
            ),
            max(0.0, time_left() - 0.35),
        )
        w = list(best_order)
        passes = 0
        max_passes = 9 if tight_relo else 5
        while passes < max_passes and t.time() < t_end and time_left() > 0.22:
            passes += 1
            improved = False
            for i in range(n_K - 1):
                if t.time() > t_end or time_left() < 0.16:
                    break
                w2 = list(w)
                w2[i], w2[i + 1] = w2[i + 1], w2[i]
                a, r, p = run_greedy(w2)
                if p > best_profit:
                    best_assignment, best_relocation, best_profit = a, r, p
                    best_order = w2
                    w = w2
                    improved = True
            if not improved:
                break

    insertion_improve()
    adjacent_sweep()

    # Optional reject: skipping low-R orders can free cars / relocation budget.
    def drop_pass():
        nonlocal best_assignment, best_relocation, best_profit
        backbone = list(best_order)
        acc = [oid for oid in keys if best_assignment[oid - 1] != -1]
        acc.sort(key=lambda oid: R[oid])
        if stressed:
            n_try = min(48, max(14, len(acc) // 2))
        else:
            n_try = min(28, max(6, len(acc) // 3))
        for oid in acc[:n_try]:
            if time_left() < 0.1:
                break
            a, r, p = run_greedy(backbone, reject_set={oid})
            if p > best_profit:
                best_assignment, best_relocation, best_profit = a, r, p
        # Pair-drop frees relocation budget (S5) or cars for better mix (S4 high load).
        if tight_relo and len(acc) >= 8:
            low = acc[: min(10, len(acc))]
            for i, j in itertools.combinations(low[:7], 2):
                if time_left() < 0.06:
                    return
                a, r, p = run_greedy(backbone, reject_set={i, j})
                if p > best_profit:
                    best_assignment, best_relocation, best_profit = a, r, p
            # Triple-drop on lowest-R accepted (C(7,3)=35 greedies; frees relocation under tight B).
            low3 = acc[: min(9, len(acc))]
            for trip in itertools.combinations(low3[:7], 3):
                if time_left() < 0.04:
                    return
                a, r, p = run_greedy(backbone, reject_set=set(trip))
                if p > best_profit:
                    best_assignment, best_relocation, best_profit = a, r, p
        elif high_order_load and (not tight_relo) and len(acc) >= 14:
            low = acc[: min(9, len(acc))]
            for i, j in itertools.combinations(low[:7], 2):
                if time_left() < 0.05:
                    return
                a, r, p = run_greedy(backbone, reject_set={i, j})
                if p > best_profit:
                    best_assignment, best_relocation, best_profit = a, r, p

    drop_pass()

    # After drop_pass the sequence / accept set changed; repeat light local moves.
    insertion_improve()
    adjacent_sweep()

    # Shuffle a small subset of positions (polynomial trials; breaks order plateaus on tight B).
    if tight_relo and n_K >= 10 and time_left() > 0.18:
        rng_v = np.random.default_rng((hash(file_path) % 1000003) + 901)
        nsw = min(72, max(24, int(18.0 + 0.55 * float(n_K))))
        for _ in range(nsw):
            if time_left() < 0.06:
                break
            w = list(best_order)
            k_sub = min(8, n_K)
            idxs = [int(x) for x in rng_v.choice(n_K, size=k_sub, replace=False)]
            sub = [w[i] for i in idxs]
            prm = rng_v.permutation(len(sub))
            sub2 = [sub[int(prm[k])] for k in range(len(sub))]
            for pos, val in zip(idxs, sub2):
                w[pos] = val
            a, r, p = run_greedy(w)
            if p > best_profit:
                best_assignment, best_relocation, best_profit = a, r, p
                best_order = w

    # Extra chain pass for tight-B instances (S5 ultra n_K~58 benefits from deeper BFS).
    if tight_relo and n_K <= 95 and time_left() > 0.85:
        n_rej = sum(1 for o in keys if best_assignment[o - 1] == -1)
        if n_rej > 0:
            chain_improve(
                best_order,
                time_cap=min(22.0, 0.38 * time_budget, max(0.0, time_left() - 0.32)),
                seed_cap=min(30, max(8, n_rej + 4)),
                max_depth=6,
                branch_k=4 if n_K >= 44 else 3,
            )

    # Tight relocation budget: extra backbone greedies (S5). Small n_K => cheap; avoids huge negative profit.
    if tight_relo and time_left() > 0.22:
        poor = best_profit < max(0.0, -0.12 * total_R) or best_profit < 0.0
        small_tight = n_K <= 95
        if poor or small_tight:
            salvage_lists = [
                list(
                    sorted(
                        keys,
                        key=lambda o: (
                            reloc_lb_to_pickup[o],
                            -R[o] / max(1.0, float(reloc_lb_to_pickup[o])),
                            pickup_deadline(o),
                            o,
                        ),
                    )
                ),
                list(alt_reloc_value),
                list(alt_deadline),
                list(alt_tight_budget),
                list(alt_short_dur),
                list(reversed(alt_deadline)),
            ]
            if alt_zero_first is not None:
                salvage_lists.append(list(alt_zero_first))
            seen = set()
            for od in salvage_lists:
                if time_left() < 0.08:
                    break
                sig = tuple(od)
                if sig in seen:
                    continue
                seen.add(sig)
                a, r, p = run_greedy(od)
                if p > best_profit:
                    best_assignment, best_relocation, best_profit = a, r, p
                    best_order = list(od)
            # Defer a few "expensive to reach" orders to the end of the backbone (often unlocks S5).
            if small_tight and time_left() > 0.06:
                defer_cands = sorted(
                    keys,
                    key=lambda o: (-float(reloc_lb_to_pickup[o]), R[o], o),
                )[:6]
                for w in defer_cands:
                    if time_left() < 0.04:
                        break
                    od = [x for x in best_order if x != w] + [w]
                    a, r, p = run_greedy(od)
                    if p > best_profit:
                        best_assignment, best_relocation, best_profit = a, r, p
                        best_order = list(od)
            # Last resort: random order shuffles (tiny n_K, tight B — cheap; breaks pathological order traps).
            if small_tight and time_left() > 0.35:
                rng_s = np.random.default_rng(90210 + n_K)
                extra = min(420, max(72, int(48.0 + 72.0 * time_left())))
                for _ in range(extra):
                    if time_left() < 0.1:
                        break
                    od = list(keys)
                    perm = rng_s.permutation(len(od))
                    od = [od[int(perm[i])] for i in range(len(od))]
                    a, r, p = run_greedy(od)
                    if p > best_profit:
                        best_assignment, best_relocation, best_profit = a, r, p
                        best_order = list(od)

    # Force high-R rejected orders to the front of the backbone (order-sensitive greedy; S5).
    if tight_relo and time_left() > 0.1:
        top_rej = sorted(
            [o for o in keys if best_assignment[o - 1] == -1],
            key=lambda o: -R[o],
        )[:26]
        for oid in top_rej:
            if time_left() < 0.045:
                break
            pref = [oid] + [x for x in best_order if x != oid]
            a, r, p = run_greedy(pref)
            if p > best_profit:
                best_assignment, best_relocation, best_profit = a, r, p
                best_order = list(pref)
        for i, j in itertools.combinations(top_rej[:9], 2):
            if time_left() < 0.035:
                break
            pref = [i, j] + [x for x in best_order if x not in (i, j)]
            a, r, p = run_greedy(pref)
            if p > best_profit:
                best_assignment, best_relocation, best_profit = a, r, p
                best_order = list(pref)

    # Drop one low-R accepted order under alternate backbones (same reject_set idea, different order dynamics).
    if tight_relo and time_left() > 0.08:
        acc_lo = sorted(
            [o for o in keys if best_assignment[o - 1] != -1],
            key=lambda o: R[o],
        )[:18]
        backs = (best_order, alt_deadline, alt_reloc_value, alt_tight_balanced)
        for bk in backs:
            if time_left() < 0.035:
                break
            for a in acc_lo[:12]:
                if time_left() < 0.03:
                    break
                a2, r2, p2 = run_greedy(list(bk), reject_set={a})
                if p2 > best_profit:
                    best_assignment, best_relocation, best_profit = a2, r2, p2
                    best_order = list(bk)

    # ------------------------------------------------------------------
    # Advanced methods (no solver): Lagrangian-style λ sweep, scalarized
    # backbones, LNS, path relinking, bounded window permutations, short
    # route-chain prefixes (all polynomial / bounded enumeration).
    # ------------------------------------------------------------------
    def _can_follow_time(oid_a, oid_b):
        """Loose time feasibility: car finishes order a, relocates to b pickup by b deadline."""
        ra = orders[oid_a]
        rb = orders[oid_b]
        finish_ready = ra[4] + timedelta(hours=4)
        travel_m = int(T[(ra[2], rb[1])])
        arrive = finish_ready + timedelta(minutes=travel_m)
        dl = rb[3] - timedelta(minutes=30)
        if dl < planning_start:
            dl = planning_start
        return arrive <= dl

    def lagrangian_pickup_sweep():
        nonlocal best_assignment, best_relocation, best_profit, best_order
        if time_left() < 0.35:
            return
        lam_grid = (
            0.0,
            0.03,
            0.06,
            0.1,
            0.15,
            0.22,
            0.32,
            0.45,
            0.62,
        )
        for lam in lam_grid:
            if time_left() < 0.12:
                break
            lag_pickup_weight[0] = float(lam)
            a, r, p = run_greedy(list(best_order))
            if p > best_profit:
                best_assignment, best_relocation, best_profit = a, r, p
                best_order = list(best_order)
        lag_pickup_weight[0] = 0.0

    def scalarized_backbones():
        nonlocal best_assignment, best_relocation, best_profit, best_order
        if time_left() < 0.28:
            return
        ord_reloc_first = sorted(
            keys,
            key=lambda o: (float(reloc_lb_to_pickup[o]), orders[o][3], -R[o], o),
        )
        a, r, p = run_greedy(ord_reloc_first)
        if p > best_profit:
            best_assignment, best_relocation, best_profit = a, r, p
            best_order = list(ord_reloc_first)
        ord_density = sorted(
            keys,
            key=lambda o: (
                -(R[o] / max(1.0, float(reloc_lb_to_pickup[o]) ** 0.55)),
                orders[o][3],
                o,
            ),
        )
        a, r, p = run_greedy(ord_density)
        if p > best_profit:
            best_assignment, best_relocation, best_profit = a, r, p
            best_order = list(ord_density)

    def lns_improve():
        """Large neighborhood: shuffle a chunk to the end of the backbone, re-greedy."""
        nonlocal best_assignment, best_relocation, best_profit, best_order
        if n_K < 6 or time_left() < 0.25:
            return
        rng_l = np.random.default_rng(20260204)
        nit = min(42, max(10, n_K // 2))
        for _ in range(nit):
            if time_left() < 0.12:
                break
            bo = list(best_order)
            n = len(bo)
            k = max(2, min(n - 1, int(0.06 * float(n) + 2.5)))
            idxs = rng_l.choice(n, size=k, replace=False)
            idx_set = set(int(x) for x in idxs)
            removed = [bo[i] for i in sorted(idx_set)]
            rest = [bo[i] for i in range(n) if i not in idx_set]
            new_o = rest + removed
            a, r, p = run_greedy(new_o)
            if p > best_profit:
                best_assignment, best_relocation, best_profit = a, r, p
                best_order = new_o

    def path_relink(target_order):
        """Gradually permute current backbone toward a target permutation; periodic re-greedy."""
        nonlocal best_assignment, best_relocation, best_profit, best_order
        if n_K < 5 or time_left() < 0.22:
            return
        rng_p = np.random.default_rng(131415)
        cur = list(best_order)
        tgt = list(target_order)
        wend = t.time() + min(5.5, max(0.0, time_left() * 0.14))
        max_sw = min(140, n_K * n_K // 2)
        steps = 0
        while steps < max_sw and t.time() < wend and time_left() > 0.1:
            diff = [i for i in range(n_K) if cur[i] != tgt[i]]
            if not diff:
                break
            i = int(rng_p.choice(diff))
            want_oid = tgt[i]
            j = next((jj for jj in range(n_K) if cur[jj] == want_oid), None)
            if j is None:
                break
            cur[i], cur[j] = cur[j], cur[i]
            steps += 1
            if steps % 4 == 0:
                a, r, p = run_greedy(cur)
                if p > best_profit:
                    best_assignment, best_relocation, best_profit = a, r, p
                    best_order = list(cur)
        a, r, p = run_greedy(cur)
        if p > best_profit:
            best_assignment, best_relocation, best_profit = a, r, p
            best_order = list(cur)

    def contiguous_window_permute_pass():
        """Brute-force permutations on short contiguous slices of the best backbone."""
        nonlocal best_assignment, best_relocation, best_profit, best_order
        if n_K < 8 or time_left() < 0.2:
            return
        w = min(6, n_K // 2)
        if w < 4:
            return
        rng_w = np.random.default_rng(70707)
        backbone = list(best_order)
        nperm_cap = 36
        n_starts = min(14, max(3, n_K // 5))
        starts = rng_w.choice(n_K - w + 1, size=min(n_starts, n_K - w + 1), replace=False)
        for st in starts:
            if time_left() < 0.08:
                break
            st = int(st)
            seg = backbone[st : st + w]
            for k, perm in enumerate(itertools.permutations(seg)):
                if k >= nperm_cap or time_left() < 0.06:
                    break
                new_o = backbone[:st] + list(perm) + backbone[st + w :]
                a, r, p = run_greedy(new_o)
                if p > best_profit:
                    best_assignment, best_relocation, best_profit = a, r, p
                    best_order = new_o
                    backbone = list(new_o)

    def route_chain_prefix_pass():
        """Bounded depth-first chains (time-feasible edges) as prefixes + deadline tail."""
        nonlocal best_assignment, best_relocation, best_profit, best_order
        if n_K > 90 or n_K < 6 or time_left() < 0.22:
            return
        tail = [x for x in alt_deadline]
        tail_set = set(tail)
        starters = sorted(keys, key=lambda o: (-R[o], orders[o][3]))[: min(6, n_K)]
        seen_prefix = set()
        budget_chains = 48

        def dfs_chain(cur_path, remaining):
            nonlocal best_assignment, best_relocation, best_profit, best_order, budget_chains
            if budget_chains <= 0 or time_left() < 0.07:
                return
            if len(cur_path) >= min(4, n_K // 2):
                pref = list(cur_path)
                keyp = tuple(pref)
                if keyp not in seen_prefix:
                    seen_prefix.add(keyp)
                    budget_chains -= 1
                    rest = [x for x in tail if x not in set(pref)]
                    new_o = pref + rest
                    a, r, p = run_greedy(new_o)
                    if p > best_profit:
                        best_assignment, best_relocation, best_profit = a, r, p
                        best_order = list(new_o)
                return
            last = cur_path[-1]
            cands = sorted(
                remaining,
                key=lambda j: (float(T[(orders[last][2], orders[j][1])]), orders[j][3]),
            )[:8]
            for j in cands:
                if j in cur_path:
                    continue
                if not _can_follow_time(last, j):
                    continue
                dfs_chain(cur_path + (j,), remaining - {j})

        for s in starters:
            if budget_chains <= 0 or time_left() < 0.06:
                break
            dfs_chain((s,), tail_set - {s})

    lagrangian_pickup_sweep()
    scalarized_backbones()
    lns_improve()
    if time_left() > 0.18:
        path_relink(alt_deadline)
    if time_left() > 0.15:
        path_relink(list(reversed(alt_deadline)))
    contiguous_window_permute_pass()
    route_chain_prefix_pass()

    return best_assignment, best_relocation

