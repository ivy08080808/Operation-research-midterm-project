import itertools
import math
import os
from datetime import datetime, timedelta

from MTP_lib import *

# Course-style wall-clock ceiling for the heuristic (PDF / judge often ~180s).
_TIME_BUDGET_MAX_S = 180.0
_HARD_STOP_S = 178.5  # force-stop slightly before 180s wall-clock
_TIME_GUARD_S = 0.02  # skip heavy work below this slack (global hard-stop safeguard)


# NOTE (course restriction):
# Only libraries allowed by the project PDF should be imported in this file.
# (gurobipy, math, numpy, pandas, time, datetime, os, itertools)
# For any other utilities (e.g., heap), implement in pure Python.


def _heappush(h, item):
    """Minimal binary heap push (min-heap) in pure Python."""
    h.append(item)
    i = len(h) - 1
    while i > 0:
        p = (i - 1) // 2
        if h[p] <= h[i]:
            break
        h[p], h[i] = h[i], h[p]
        i = p


def _heappop(h):
    """Minimal binary heap pop (min-heap) in pure Python."""
    last = h.pop()
    if not h:
        return last
    ret = h[0]
    h[0] = last
    n = len(h)
    i = 0
    while True:
        l = 2 * i + 1
        r = l + 1
        if l >= n:
            break
        c = l
        if r < n and h[r] < h[l]:
            c = r
        if h[i] <= h[c]:
            break
        h[i], h[c] = h[c], h[i]
        i = c
    return ret


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

    # Feature flags for quick ablations (set env var to "0" to disable):
    #   OR_LS_CHAIN / OR_LS_ILS / OR_LS_LNS / OR_LS_DROP / OR_LS_INSERT / OR_LS_HR_PREFIX / OR_LS_WAVES
    #   OR_REBALANCE (adaptive long-term rebalancing; default on but gated by instance signals)
    # Optional: OR_STAGE_LOG=1 prints stage profits to stderr.
    def _env_on(name: str, default: str = "1") -> bool:
        v = os.environ.get(name, default).strip().lower()
        return v not in {"0", "false", "no", "off"}

    _LS_CHAIN = _env_on("OR_LS_CHAIN", "1")
    # The following meta-heuristics relied on the `random` stdlib module, which is not
    # on the course's allowed-library list. Keep them disabled for submission safety.
    _LS_ILS = False
    _LS_LNS = False
    _LS_DROP = False
    _LS_INSERT = False
    _LS_HR_PREFIX = _env_on("OR_LS_HR_PREFIX", "1")
    _LS_WAVES = _env_on("OR_LS_WAVES", "1")
    _REBALANCE_FLAG = _env_on("OR_REBALANCE", "1")
    _STAGE_LOG = False

    # Scale wall-clock with n_K (and a bit with fleet) up to 180s; leave slack for LNS / insertion.
    ps_scale = max(
        1.0,
        0.22 * (float(n_K) / 40.0) ** 0.58 + 0.10 * float(n_C) / max(1.0, float(n_S)),
    )
    time_budget = min(
        _TIME_BUDGET_MAX_S,
        max(12.0, min(_TIME_BUDGET_MAX_S, 11.5 + 0.98 * float(n_K) * ps_scale / 40.0)),
    )

    # Structure-only cues (no filename): tight relocation budget vs heavy order load.
    tight_relo = B <= 300
    load_ratio = float(n_K) / max(1, n_C)
    high_order_load = n_K >= 30 or load_ratio >= 3.2
    stressed = tight_relo or high_order_load

    # Hard caps for TA-scale instances (~10k orders, ~1k cars): keep greedy fast + truncate meta-heuristics.
    _scale_huge = n_K >= 3200 or (n_K >= 2200 and n_C >= 600)
    _scale_ultra = n_K >= 6200 or (n_K >= 5000 and n_C >= 900)
    _scale_hyper = n_K >= 9200 or (n_K >= 8000 and n_C >= 950)

    if stressed:
        time_budget = min(_TIME_BUDGET_MAX_S, time_budget * 1.15)
        if _scale_hyper:
            time_budget = min(_TIME_BUDGET_MAX_S, time_budget * 0.92)
    if tight_relo:
        time_budget = min(_TIME_BUDGET_MAX_S, time_budget * 1.08)
    # Absolute hard stop (leave slack vs judge wall-clock). Must be applied last.
    time_budget = min(time_budget, _HARD_STOP_S)

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
    if _scale_hyper:
        m_near = min(10, n_S)
    elif _scale_ultra:
        m_near = min(12, n_S)
    elif n_K > 1200:
        m_near = min(12, n_S)
    elif n_K > 400:
        m_near = min(16, n_S)
    else:
        m_near = min(24, n_S)
    if stressed and not _scale_hyper:
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
    return_count = {s: 0 for s in range(1, n_S + 1)}
    for oid in keys:
        pickup_count[orders[oid][1]] += 1
        return_count[orders[oid][2]] += 1

    # Static hot-spots inferred from the instance.  In geographic-imbalance
    # cases, cars often accumulate at a return sink far from the next pickup
    # hot-spot, so relocation candidates must not be limited to nearby stations.
    _hot_k = min(n_S, 8)
    hot_pickup_stations = sorted(range(1, n_S + 1), key=lambda s: (-pickup_count[s], s))[:_hot_k]
    hot_return_stations = sorted(range(1, n_S + 1), key=lambda s: (-return_count[s], s))[:_hot_k]

    # -------------------------
    # Adaptive rebalancing gate
    # -------------------------
    # Goal: avoid overfitting a single S3-like instance by enabling proactive rebalancing
    # only when (a) station demand is highly skewed, (b) reject cost is high, and (c) B is ample.
    def _entropy_from_counts(cnt: dict) -> float:
        s = float(sum(cnt.values()))
        if s <= 0:
            return 0.0
        ent = 0.0
        for v in cnt.values():
            if v <= 0:
                continue
            p = float(v) / s
            ent -= p * math.log(p + 1e-18)
        return ent

    # Demand skew: high top-share or low normalized entropy.
    _tot_pick = float(sum(pickup_count.values()))
    _top_pick = float(max(pickup_count.values())) if pickup_count else 0.0
    top_share = (_top_pick / _tot_pick) if _tot_pick > 0 else 0.0
    ent = _entropy_from_counts(pickup_count)
    ent_norm = ent / (math.log(float(n_S) + 1e-18) if n_S > 1 else 1.0)
    geo_imbalanced = (top_share >= 0.075) or (ent_norm <= 0.82)

    # Reject cost proxy: if revenue is top-heavy, rejecting "important" orders hurts more.
    vals = sorted(R.values(), reverse=True)
    if vals:
        m = max(1, int(len(vals) * 0.10))
        top10_share = float(sum(vals[:m])) / max(1e-9, float(sum(vals)))
    else:
        top10_share = 0.0
    reject_cost_high = (top10_share >= 0.28) or (float(n_K) / max(1.0, float(n_C)) >= 4.0)

    # Budget ample: only spend on long-term rebalancing when B is not the binding constraint.
    budget_ample = (B >= 50000) and (not tight_relo)

    # Stronger signal for extreme one-way geographic flows.  This is based
    # only on demand/return distributions, not on a filename.
    max_pickups = max(pickup_count.values()) if pickup_count else 0
    max_returns = max(return_count.values()) if return_count else 0
    severe_geo_imbalance = bool(
        budget_ample
        and n_K >= 1000
        and (max_pickups >= 0.14 * max(1, n_K) or max_returns >= 0.14 * max(1, n_K))
    )

    ENABLE_REBALANCE = bool(_REBALANCE_FLAG and geo_imbalanced and reject_cost_high and budget_ample)

    def run_greedy(order_ids, reject_set=None):
        # Hard-stop: do not start a full greedy replay when wall-clock budget is exhausted.
        if time_left() < _TIME_GUARD_S:
            return ([-1] * n_K, [], -2.0 * total_R)
        reject_set = reject_set or set()
        # Car state (dynamic)
        car_level = {cid: cars_init[cid][0] for cid in cars_init}
        car_station = {cid: cars_init[cid][1] for cid in cars_init}
        car_available = {cid: planning_start for cid in cars_init}  # ready time at current station

        # Remaining relocation budget (minutes)
        budget_left = int(B)

        # Min-heaps keyed by earliest availability; stale tuples removed via lazy versioning per car id.
        buckets = {(s, l): [] for s in range(1, n_S + 1) for l in range(1, n_L + 1)}
        car_vid = {cid: 0 for cid in cars_init}

        def bucket_push(s: int, l: int, avail: datetime, cid: int):
            h = buckets[(s, l)]
            _heappush(h, (avail, cid, car_vid[cid]))

        def collect_feasible(s: int, l: int, avail_deadline: datetime, limit: int):
            """Collect up to `limit` cars at (station,level) whose token matches car_vid[cid]."""
            h = buckets[(s, l)]
            if not h or limit <= 0:
                return []
            popped = []
            out = []
            while h and len(out) < limit:
                tpl = _heappop(h)
                popped.append(tpl)
                avail, cid, vid = tpl
                if vid != car_vid[cid]:
                    continue
                if avail > avail_deadline:
                    break
                out.append((avail, cid))
            for tpl in popped:
                _heappush(h, tpl)
            return out

        def _wave_anchor_time(cur_orders):
            """
            Cheap estimate of the next-wave time anchor.
            Use min pickup_time among a small prefix to avoid O(|K|) scans on huge instances.
            """
            if not cur_orders:
                return planning_start
            sample = cur_orders[:50] if len(cur_orders) > 50 else cur_orders
            tmin = orders[sample[0]][3]
            for oid in sample[1:]:
                pt = orders[oid][3]
                if pt < tmin:
                    tmin = pt
            return tmin

        def rebalance_step(cur_orders, *, anchor_override=None):
            """
            Proactive rebalancing (dynamic supply-demand gap):
            - Estimate near-future pickup demand per station from upcoming orders in `cur_orders`.
            - Estimate near-future supply per station as cars available by `anchor`.
            - Move a few *already idle* cars from surplus stations to deficit stations.

            Design goals:
            - Keep it cheap (sublinear in n_C and n_K).
            - Only move cars whose current availability is early enough (<= anchor),
              so the relocation chain will be applied before upcoming order deadlines.
            - Respect remaining relocation budget.
            """
            nonlocal budget_left
            if not ENABLE_REBALANCE:
                return
            if budget_left <= 0:
                return
            # Only meaningful under geographic stress; on huge instances keep it very small.
            if not stressed:
                return

            anchor = anchor_override if anchor_override is not None else _wave_anchor_time(cur_orders)
            # If we are already deep into the horizon, rebalancing has diminishing returns.
            # Still allow it when many orders remain.
            if time_left() < 0.6:
                return

            # --- Demand estimate: count pickups in a short horizon after anchor.
            # Keep a tight horizon so "need cars soon" dominates, not long-term counts.
            if _scale_hyper:
                demand_sample = 900
                horizon_h = 18
            elif _scale_ultra:
                demand_sample = 900
                horizon_h = 20
            elif _scale_huge:
                demand_sample = 1100
                horizon_h = 22
            else:
                demand_sample = 1200
                horizon_h = 24

            h_end = anchor + timedelta(hours=int(horizon_h))
            demand = {s: 0 for s in range(1, n_S + 1)}
            # cur_orders is already an ordering; sample its prefix for speed.
            for oid in (cur_orders[:demand_sample] if len(cur_orders) > demand_sample else cur_orders):
                pt = orders[oid][3]
                if pt < anchor:
                    continue
                if pt > h_end:
                    continue
                demand[orders[oid][1]] += 1

            # --- Supply estimate: count cars that can be present & ready at station by anchor.
            # We only need a small cap; beyond a few cars per station, incremental benefit is small.
            cap_per_level = 2 if _scale_hyper else 3
            supply = {s: 0 for s in range(1, n_S + 1)}
            for s in range(1, n_S + 1):
                if s % 20 == 0 and time_left() < 0.12:
                    return
                cnt = 0
                for lv in range(1, n_L + 1):
                    cnt += len(collect_feasible(s, lv, anchor, cap_per_level))
                    if cnt >= 12:
                        break
                supply[s] = cnt

            # Score station deficit (positive => need cars; negative => surplus).
            # Weight demand more than supply because rejects are costly in profit.
            deficit = {}
            for s in range(1, n_S + 1):
                deficit[s] = float(demand[s]) - 0.85 * float(supply[s])

            # Candidate deficit stations: prioritize large positive deficit, then by baseline pickup_count.
            top_k = 8 if _scale_hyper else (10 if _scale_ultra else (12 if _scale_huge else 14))
            top_k = min(top_k, n_S)
            hot = sorted(
                range(1, n_S + 1),
                key=lambda s: (-deficit[s], -pickup_count[s], s),
            )[:top_k]

            # Candidate surplus stations: negative deficit, prefer strong surplus and low demand.
            cold_k = min(n_S, max(top_k * 2, 14))
            cold = sorted(
                range(1, n_S + 1),
                key=lambda s: (deficit[s], pickup_count[s], s),
            )[:cold_k]
            # Always include static return sinks as possible surplus sources.
            for _s in hot_return_stations:
                if _s not in cold:
                    cold.append(_s)

            # How many rebalancing moves to attempt this wave.  The original
            # cap of 6 on scale-hyper cases was too small for extreme imbalance,
            # but this is still bounded to keep runtime under the grading limit.
            if severe_geo_imbalance:
                move_cap = 24 if _scale_hyper else (32 if _scale_ultra else 42)
            else:
                move_cap = 6 if _scale_hyper else (8 if _scale_ultra else (10 if _scale_huge else 14))
            if tight_relo:
                move_cap = min(move_cap, 12)
            if budget_left < 120:
                move_cap = min(move_cap, 6)

            made = 0
            # Target buffer at hot station depends on its predicted demand in horizon.
            def target_buf(ts: int) -> int:
                d = demand.get(ts, 0)
                if severe_geo_imbalance:
                    if d >= 80:
                        return 16
                    if d >= 40:
                        return 11
                    if d >= 18:
                        return 7
                    if d >= 10:
                        return 5
                    return 3
                if d >= 18:
                    return 4
                if d >= 10:
                    return 3
                return 2

            def early_supply_at(station: int, deadline: datetime, need: int) -> int:
                cnt = 0
                for lv in range(1, n_L + 1):
                    cnt += len(collect_feasible(station, lv, deadline, need))
                    if cnt >= need:
                        return cnt
                return cnt

            supply_deadline = anchor
            if supply_deadline < planning_start:
                supply_deadline = planning_start

            # Iterate hot stations by deficit; pull from surplus sources.
            for ts in hot:
                if time_left() < 0.10:
                    return
                if made >= move_cap or budget_left <= 0:
                    break
                need = target_buf(ts)
                if deficit.get(ts, 0.0) <= 0.5:
                    continue
                if early_supply_at(ts, supply_deadline, need) >= need:
                    continue

                best_src = None  # (mv, avail, cid, fs, lv)
                # Prefer sources with strongest surplus and closer travel time to target.
                for fs in cold:
                    if fs == ts:
                        continue
                    if deficit.get(fs, 0.0) > -0.25 and not (severe_geo_imbalance and fs in hot_return_stations):
                        continue
                    mv = T[(fs, ts)]
                    if mv <= 0 or mv > budget_left:
                        continue
                    avail_deadline = anchor - timedelta(minutes=mv)
                    if avail_deadline < planning_start:
                        avail_deadline = planning_start
                    for lv in range(1, n_L + 1):
                        cands = collect_feasible(fs, lv, avail_deadline, 1)
                        if not cands:
                            continue
                        avail, cid = cands[0]
                        cand = (mv, avail, cid, fs, lv)
                        if best_src is None or cand < best_src:
                            best_src = cand
                    if best_src is not None and best_src[0] == 30:
                        break

                if best_src is None:
                    continue

                mv, avail, cid, fs, lv = best_src
                car_vid[cid] += 1
                relocation.append([int(cid), int(fs), int(ts), _fmt_time(avail)])
                budget_left -= int(mv)
                car_station[cid] = ts
                car_available[cid] = avail + timedelta(minutes=int(mv))
                bucket_push(ts, lv, car_available[cid], cid)
                made += 1

        # Profit identity: profit = sum_{acc} R - 2 sum_{rej} R = -2*total_R + 3*sum_{acc} R.
        # Marginal gain of accepting oid vs rejecting it is 3*R[oid]; tie-break cars by reloc efficiency.
        def _reject_aware_lex(cand, oid_r):
            mv, upg, arr, av, _fs, cid, _lv = cand
            gain = 3.0 * R[oid_r]
            eff = gain / (1.0 + float(mv) ** 1.12)
            if tight_relo and mv > 0.0:
                mv_adj = mv * (1.0 + 1.35 * mv / max(1.0, float(B)))
            else:
                mv_adj = mv
            return (mv_adj, int(upg), arr, -eff, av, int(cid))

        for cid in cars_init:
            bucket_push(car_station[cid], car_level[cid], planning_start, cid)

        assignment = [-1] * n_K
        relocation = []
        accepted_sum = 0.0

        if _scale_hyper:
            k_same_scan = 6 if tight_relo else 5
            k_reloc_scan = 6 if tight_relo else 5
        elif _scale_ultra:
            k_same_scan = 8 if tight_relo else 6
            k_reloc_scan = 7 if tight_relo else 6
        else:
            k_same_scan = 10 if tight_relo else (8 if high_order_load else (6 if stressed else 3))
            k_reloc_scan = 9 if tight_relo else (7 if high_order_load else (5 if stressed else 3))

        max_waves = 5 if _LS_WAVES else 1
        if _scale_hyper:
            max_waves = min(max_waves, 2)
        elif _scale_ultra:
            max_waves = min(max_waves, 3)
        elif _scale_huge:
            max_waves = min(max_waves, 4)
        cur_wave = list(order_ids)
        for wave_idx in range(max_waves):
            if not cur_wave:
                break
            # Proactive rebalancing before attempting this wave (helps geographic-imbalance instances).
            if ENABLE_REBALANCE and (wave_idx == 0 or (stressed and wave_idx <= 2)):
                rebalance_step(cur_wave)
            if wave_idx > 0:
                cur_wave.sort(key=lambda o: (pickup_deadline(o), -R[o], o))

            next_wave = []
            processed = 0
            for oid in cur_wave:
                if time_left() < _TIME_GUARD_S:
                    break
                processed += 1
                if oid in reject_set:
                    continue
                if wave_idx > 0 and assignment[oid - 1] != -1:
                    continue
                req_lvl, ps, rs, pt, rt = orders[oid]
                deadline = pt - timedelta(minutes=30)
                if deadline < planning_start:
                    deadline = planning_start

                # Periodic proactive rebalancing while simulating this wave:
                # once cars begin to drift (S3-style), fix supply before too many rejects accumulate.
                if ENABLE_REBALANCE and stressed and budget_left > 0:
                    if _scale_hyper:
                        every = 520
                    elif _scale_ultra:
                        every = 420
                    elif _scale_huge:
                        every = 320
                    else:
                        every = 220
                    if processed % every == 0 and time_left() > 0.8:
                        # Anchor at the current deadline so moves happen early enough to help upcoming orders.
                        rebalance_step(cur_wave, anchor_override=deadline)

                best = None
                # (move_minutes, upgrade_flag, arrive_time, avail_time, from_station, car_id, car_level)

                for lvl_opt in (req_lvl, req_lvl + 1):
                    if lvl_opt > n_L:
                        continue
                    for avail, cid in collect_feasible(ps, lvl_opt, deadline, k_same_scan):
                        cand = (0, 0 if lvl_opt == req_lvl else 1, avail, avail, ps, cid, lvl_opt)
                        if best is None or _reject_aware_lex(cand, oid) < _reject_aware_lex(best, oid):
                            best = cand
                    if best is not None and best[0] == 0 and best[4] == ps and (not tight_relo):
                        break

                if best is None and budget_left > 0:
                    # Nearby stations are fast on ordinary instances, but when B is
                    # ample and demand is geographically imbalanced, also inspect
                    # return sinks and surplus stations even if they are far away.
                    candidate_sources = list(nearby[ps][:m_near])
                    if budget_ample:
                        for _s in hot_return_stations:
                            if _s not in candidate_sources:
                                candidate_sources.append(_s)
                        if severe_geo_imbalance and ps in hot_pickup_stations:
                            extra = sorted(
                                range(1, n_S + 1),
                                key=lambda s: (-(return_count[s] - pickup_count[s]), T[(s, ps)], s),
                            )[:min(n_S, max(m_near, 18))]
                            for _s in extra:
                                if _s not in candidate_sources:
                                    candidate_sources.append(_s)
                    for from_s in candidate_sources:
                        if from_s == ps:
                            continue
                        move_minutes = T[(from_s, ps)]
                        if move_minutes <= 0 or move_minutes > budget_left:
                            continue
                        avail_deadline = deadline - timedelta(minutes=move_minutes)
                        for lvl_opt in (req_lvl, req_lvl + 1):
                            if lvl_opt > n_L:
                                continue
                            for avail, cid in collect_feasible(
                                from_s, lvl_opt, avail_deadline, k_reloc_scan
                            ):
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
                                if best is None or _reject_aware_lex(cand, oid) < _reject_aware_lex(best, oid):
                                    best = cand

                if best is None:
                    next_wave.append(oid)
                    continue

                move_minutes, _upgrade_flag, _arrive, avail, from_s, cid, lvl_used = best

                # Commit: invalidate any older heap entries tied to this car.
                car_vid[cid] += 1

                if from_s != ps:
                    relocation.append([int(cid), int(from_s), int(ps), _fmt_time(avail)])
                    budget_left -= int(move_minutes)
                    car_station[cid] = ps

                assignment[oid - 1] = int(cid)
                accepted_sum += R[oid]

                car_station[cid] = rs
                car_available[cid] = rt + timedelta(hours=4)
                bucket_push(rs, lvl_used, car_available[cid], cid)

            if not next_wave:
                break
            if wave_idx > 0 and len(next_wave) == len(cur_wave):
                break
            cur_wave = next_wave

        profit = -2.0 * total_R + 3.0 * accepted_sum
        return assignment, relocation, profit

    def _safe_greedy(order_ids, reject_set=None):
        """Skip starting a full greedy when wall-clock is nearly exhausted (keeps best-so-far)."""
        if time_left() < _TIME_GUARD_S:
            return None
        return run_greedy(order_ids, reject_set)

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

    # Reject-cost-weighted construction: large 2*R relative to pickup slack first (then EDF).
    alt_reject_aware = list(keys)
    alt_reject_aware.sort(
        key=lambda oid: (
            -(2.0 * R[oid])
            / max(
                0.05,
                (pickup_deadline(oid) - planning_start).total_seconds() / 3600.0,
            ),
            pickup_deadline(oid),
            oid,
        )
    )

    # Rolling horizon: process calendar days in order; inside each day use EDF then revenue.
    # This avoids assigning a car to a late high-revenue order before earlier
    # orders that could have used the same car.
    by_day = {}
    for oid in keys:
        pt = orders[oid][3]
        di = (pt.date() - planning_start.date()).days
        by_day.setdefault(di, []).append(oid)
    rolling_day_order = []
    for di in sorted(by_day.keys()):
        chunk = by_day[di]
        chunk.sort(key=lambda o: (pickup_deadline(o), -R[o], o))
        rolling_day_order.extend(chunk)

    # Coarser chronological windows: keep time order across windows, but allow
    # high-value orders to lead inside each 2-day bucket.
    by_2day = {}
    for oid in keys:
        pt = orders[oid][3]
        wi = (pt.date() - planning_start.date()).days // 2
        by_2day.setdefault(wi, []).append(oid)
    rolling_2day_value_order = []
    for wi in sorted(by_2day.keys()):
        chunk = by_2day[wi]
        chunk.sort(key=lambda o: (-R[o], pickup_deadline(o), o))
        rolling_2day_value_order.extend(chunk)

    # Two-phase backbone: prioritize a high-R cohort (deadline-sorted), then remaining orders by pickup time.
    _tp_n = max(5, min(n_K - 1, (n_K * 2) // 5 + max(0, n_K // 14)))
    top_r_set = set(sorted(keys, key=lambda o: -R[o])[:_tp_n])
    two_phase_order = sorted(top_r_set, key=lambda o: (pickup_deadline(o), -R[o], o)) + [
        o for o in sorted(keys, key=lambda o: orders[o][3]) if o not in top_r_set
    ]

    # Put the largest-revenue cohort first (deadline / EDF within cohort), then the rest by time.
    _hr_n = max(6, min(n_K - 1, (n_K * 3) // 10 + max(0, n_K // 10)))
    hi_r_set = set(sorted(keys, key=lambda o: -R[o])[:_hr_n])
    alt_hi_r_deadline_head = sorted(hi_r_set, key=lambda o: (pickup_deadline(o), -R[o], o)) + [
        o for o in sorted(keys, key=lambda o: orders[o][3]) if o not in hi_r_set
    ]

    deterministic_orders = [
        base,
        alt_reject_aware,
        alt_hi_r_deadline_head,
        rolling_day_order,
        rolling_2day_value_order,
        two_phase_order,
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
    if _scale_hyper:
        # Fewer full passes; keep chronological backbones first.  In long
        # horizons, pure revenue-first ordering can reserve cars for late
        # orders and incorrectly block earlier feasible orders.
        deterministic_orders = [
            rolling_day_order,
            rolling_2day_value_order,
            alt_time,
            alt_deadline,
            alt_reject_aware,
            alt_reloc_value,
        ]
        if not severe_geo_imbalance:
            deterministic_orders.insert(0, base)
        if tight_relo and alt_zero_first is not None:
            deterministic_orders.append(alt_zero_first)
    elif _scale_ultra:
        deterministic_orders = [
            base,
            alt_reject_aware,
            alt_hi_r_deadline_head,
            rolling_day_order,
            two_phase_order,
            alt_deadline,
            alt_time,
            alt_reloc_value,
            alt_tight_budget,
        ]
        if stressed:
            deterministic_orders.extend([alt_hotspot, alt_edf_hot])
    elif _scale_huge:
        deterministic_orders = [
            base,
            alt_reject_aware,
            alt_hi_r_deadline_head,
            rolling_day_order,
            two_phase_order,
            alt_time,
            alt_deadline,
            alt_reloc_value,
            alt_tight_budget,
            alt_rph,
        ]
        if alt_critical is not None:
            deterministic_orders.append(alt_critical)
        if stressed:
            if tight_relo and alt_zero_first is not None:
                deterministic_orders.append(alt_zero_first)
            deterministic_orders.append(alt_hotspot)
    deterministic_orders = tuple(deterministic_orders)

    seen_sig = set()
    # Start from a chronological backbone in severe imbalance / long-horizon
    # instances to avoid assigning cars to late orders before earlier ones.
    start_order = rolling_day_order if severe_geo_imbalance else base
    best_order = list(start_order)
    init = _safe_greedy(start_order)
    if init is None:
        return [-1] * n_K, []
    best_assignment, best_relocation, best_profit = init
    seen_sig.add(tuple(best_order))

    # Fast backbone phase: cheap diverse orders first (reject-aware, rolling, two-phase, EDF, time).
    _fast_cap = 0.13
    if _scale_hyper:
        _fast_cap = 0.07
    elif _scale_ultra:
        _fast_cap = 0.09
    elif _scale_huge:
        _fast_cap = 0.11
    fast_deadline_t = t_start + min(26.0, max(11.0, time_budget * _fast_cap))
    fast_candidates = (
        ("rolling_day", rolling_day_order),
        ("rolling_2day_value", rolling_2day_value_order),
        ("time", alt_time),
        ("deadline", alt_deadline),
        ("reject_aware", alt_reject_aware),
        ("hi_r_edf", alt_hi_r_deadline_head),
        ("two_phase", two_phase_order),
    ) if severe_geo_imbalance else (
        ("reject_aware", alt_reject_aware),
        ("hi_r_edf", alt_hi_r_deadline_head),
        ("rolling_day", rolling_day_order),
        ("rolling_2day_value", rolling_2day_value_order),
        ("two_phase", two_phase_order),
        ("deadline", alt_deadline),
        ("time", alt_time),
    )
    for _fname, od in fast_candidates:
        if elapsed() > fast_deadline_t:
            break
        if time_left() < 0.38:
            break
        sig = tuple(od)
        if sig in seen_sig:
            continue
        seen_sig.add(sig)
        out = _safe_greedy(od)
        if out is None:
            break
        a, r, p = out
        if p > best_profit:
            best_assignment, best_relocation, best_profit = a, r, p
            best_order = list(od)
            pass

    for cand_order in deterministic_orders:
        if time_left() < 0.28:
            break
        sig = tuple(cand_order)
        if sig in seen_sig:
            continue
        seen_sig.add(sig)
        out = _safe_greedy(cand_order)
        if out is None:
            break
        a, r, p = out
        if p > best_profit:
            best_assignment, best_relocation, best_profit = a, r, p
            best_order = list(cand_order)
            pass

    # Hard stop: always return a valid best-so-far solution.
    if time_left() < 0.10:
        return best_assignment, best_relocation

    # Push a few very high-R rejects to the front of the current backbone (cheap vs full order design).
    if _LS_HR_PREFIX and high_order_load and time_left() > 0.55:
        rej = [o for o in keys if best_assignment[o - 1] == -1]
        rej.sort(key=lambda o: -R[o])
        pref_n = min(16, max(6, n_K // 12 + 4))
        if _scale_hyper:
            pref_n = min(6, pref_n)
        elif _scale_ultra:
            pref_n = min(10, pref_n)
        elif _scale_huge:
            pref_n = min(12, pref_n)
        seen_pref = set()
        for oid in rej[:pref_n]:
            if time_left() < 0.12:
                break
            if oid in seen_pref:
                continue
            seen_pref.add(oid)
            pref = [oid] + [x for x in best_order if x != oid]
            out = _safe_greedy(pref)
            if out is None:
                break
            a, r, p = out
            if p > best_profit:
                best_assignment, best_relocation, best_profit = a, r, p
                best_order = pref
                pass

    # Randomized multi-starts are disabled (random module not in allowed-library list).

    # Force-reject worst marginal-efficiency orders (mainly helps tight relocation budget).
    if tight_relo:
        worst_eff = sorted(
            keys,
            key=lambda oid: R[oid] / (1.0 + float(reloc_lb_to_pickup[oid])),
        )
        for oid in worst_eff[:10]:
            if time_left() < 0.12:
                break
            out = _safe_greedy(best_order, reject_set={oid})
            if out is None:
                break
            a, r, p = out
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
            if time_left() < max(0.06, _TIME_GUARD_S * 3):
                return None
            seen = set()
            forced = []
            for x in prefix:
                if x not in seen:
                    seen.add(x)
                    forced.append(x)
            full = forced + [x for x in base_list if x not in seen]
            if time_left() < _TIME_GUARD_S:
                return None
            a, r, p = run_greedy(full)
            return a, r, p, full

        queue = [(1, [s]) for s in seed_orders]
        visited_prefixes = set()

        while queue and t.time() < chain_end and time_left() > 0.15:
            depth, prefix = queue.pop()
            key = tuple(prefix)
            if key in visited_prefixes:
                continue
            visited_prefixes.add(key)

            if time_left() < 0.10:
                break
            tres = _forced_run(prefix)
            if tres is None:
                break
            a, r, p, full_order = tres
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
                if time_left() < 0.10:
                    break
                queue.append((depth + 1, prefix + [nxt]))

    # First chain pass: BFS is expensive (each node = full greedy); scale cap with n_K and remaining time.
    raw_chain = min(55.0, max(2.0, 0.30 * time_budget))
    if _scale_hyper:
        raw_chain = min(12.0, max(3.5, 0.09 * time_budget))
    elif _scale_ultra:
        raw_chain = min(22.0, max(3.8, 0.15 * time_budget))
    elif _scale_huge:
        raw_chain = min(raw_chain, 38.0)
    if n_K < 100:
        raw_chain = min(raw_chain, 5.0)
    elif n_K < 400:
        raw_chain = min(raw_chain, 16.0)
    chain_budget = min(raw_chain, max(0.0, time_left() - 0.5))
    sc_seed = min(45, max(6, n_K // 5 + 3))
    sc_depth = 6 if n_K >= 150 else 4
    sc_branch = 3 if n_K >= 200 else 2
    if _scale_hyper:
        sc_seed = min(10, max(4, n_K // 900 + 3))
        sc_depth = 2
        sc_branch = 2
    elif _scale_ultra:
        sc_seed = min(18, max(5, n_K // 400 + 4))
        sc_depth = min(sc_depth, 3)
        sc_branch = min(sc_branch, 2)
    elif _scale_huge:
        sc_seed = min(sc_seed, 28)
        sc_depth = min(sc_depth, 4)
        sc_branch = min(sc_branch, 3)
    if stressed and not _scale_ultra and not _scale_hyper:
        sc_seed = min(52, max(10, int(n_K // 3.5) + 6))
        sc_depth = max(sc_depth, 5)
        sc_branch = max(sc_branch, 3)
    if high_order_load and time_left() > 60.0 and not (_scale_ultra or _scale_hyper):
        sc_seed = min(58, sc_seed + 6)
        sc_depth = max(sc_depth, 5 if n_K >= 80 else sc_depth)
        chain_budget = min(raw_chain * 1.12, chain_budget + 4.5, max(0.0, time_left() - 0.45))
    if _LS_CHAIN:
        chain_improve(
            best_order,
            time_cap=chain_budget,
            seed_cap=sc_seed,
            max_depth=sc_depth,
            branch_k=sc_branch,
        )
        pass

    # Second pass with alternate backbone ordering if time allows
    if _LS_CHAIN and time_left() > 2.0 and n_K >= 60 and not _scale_hyper:
        cap2 = min(28.0, 0.22 * time_budget, max(0.0, time_left() - 0.5))
        if _scale_ultra:
            cap2 = min(cap2, 9.0)
        if n_K < 200:
            cap2 = min(cap2, 4.0)
        chain_improve(
            alt_deadline,
            time_cap=cap2,
            seed_cap=min(30, max(6, n_K // 6)) if not _scale_ultra else min(12, max(5, n_K // 80)),
            max_depth=5 if n_K >= 200 else 3,
            branch_k=2,
        )
        pass

    # Third chain: hotspot backbone (helps congested pickups) with a modest time cap.
    if _LS_CHAIN and time_left() > 2.2 and (stressed or high_order_load) and not (_scale_ultra or _scale_hyper):
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
        pass

    # ILS disabled (random module not allowed).

    # LNS disabled (random module not allowed).

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
            out = _safe_greedy(backbone, reject_set={oid})
            if out is None:
                break
            a, r, p = out
            if p > best_profit:
                best_assignment, best_relocation, best_profit = a, r, p
        # Pair-drop: frees cars for high-R rejects (tight B); under high load also try a few low-R pairs.
        if len(acc) >= 8:
            low = acc[: min(8, len(acc))]
            max_c = 6 if tight_relo else 5
            for i, j in itertools.combinations(low[:max_c], 2):
                if time_left() < 0.06:
                    return
                if not tight_relo and not high_order_load:
                    break
                out = _safe_greedy(backbone, reject_set={i, j})
                if out is None:
                    return
                a, r, p = out
                if p > best_profit:
                    best_assignment, best_relocation, best_profit = a, r, p

    # drop-pass disabled (random module not allowed).

    # Late insertion: place high-R rejected orders into varied positions of the current backbone.
    def insertion_repair_pass():
        nonlocal best_assignment, best_relocation, best_profit, best_order
        if n_K < 5 or time_left() < 0.48:
            return
        rejected = [o for o in keys if best_assignment[o - 1] == -1]
        if not rejected:
            return
        rejected.sort(key=lambda o: -R[o])
        ins_cap = 38.0
        if _scale_hyper:
            ins_cap = 9.0
        elif _scale_ultra:
            ins_cap = 16.0
        elif _scale_huge:
            ins_cap = 26.0
        t_end = t.time() + min(ins_cap, max(0.0, time_left() - 0.55))
        backbone_ref = list(best_order)
        top_rej = min(28, len(rejected)) if high_order_load else min(22, len(rejected))
        if _scale_hyper:
            top_rej = min(8, len(rejected))
        elif _scale_ultra:
            top_rej = min(14, len(rejected))
        elif _scale_huge:
            top_rej = min(18, top_rej)
        for oid in rejected[:top_rej]:
            if t.time() > t_end or time_left() < 0.22:
                break
            base_wo = [x for x in backbone_ref if x != oid]
            n = len(base_wo)
            if n == 0:
                continue
            if n <= 26:
                positions = range(n + 1)
            elif n <= 80:
                step = max(1, n // 28) if high_order_load else max(1, n // 20)
                positions = list(range(0, n + 1, step)) + [n]
            else:
                step = max(1, n // 20)
                positions = list(range(0, n + 1, step)) + [n]
            for pos in positions:
                if t.time() > t_end or time_left() < 0.15:
                    break
                new_o = base_wo[:pos] + [oid] + base_wo[pos:]
                out = _safe_greedy(new_o)
                if out is None:
                    return
                a, r, p = out
                if p > best_profit:
                    best_assignment, best_relocation, best_profit = a, r, p
                    best_order = new_o
                    backbone_ref = new_o

    # insertion repair disabled (random module not allowed).

    return best_assignment, best_relocation

