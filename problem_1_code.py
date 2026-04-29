from gurobipy import *
from datetime import *

# Helpers

def parse_time(t):
    return datetime.strptime(t.strip(), "%Y/%m/%d %H:%M")

def hours_between(t1, t2):
    return (t2 - t1).total_seconds() / 3600


# Instance loader

def load_instance(filename):
    with open(filename, "r") as f:
        raw_lines = [l.strip() for l in f if l.strip() and "====" not in l]

    idx = 0
    idx += 1  # skip general header
    n_S, n_C, n_L, n_K, n_D, B = map(int, raw_lines[idx].split(","))
    idx += 1

    idx += 1  # skip car header
    cars = {}
    for _ in range(n_C):
        cid, lvl, st = map(int, raw_lines[idx].split(","))
        cars[cid] = (lvl, st)
        idx += 1

    idx += 1  # skip rate header
    rate = {}
    for _ in range(n_L):
        lvl, r = map(int, raw_lines[idx].split(","))
        rate[lvl] = r
        idx += 1

    idx += 1  # skip order header
    orders_raw = {}
    for _ in range(n_K):
        parts = raw_lines[idx].split(",")
        k = int(parts[0])
        orders_raw[k] = (int(parts[1]), int(parts[2]), int(parts[3]), parts[4], parts[5])
        idx += 1

    idx += 1  # skip travel header
    T = {}
    for _ in range(n_S * n_S):
        i, j, t = map(int, raw_lines[idx].split(","))
        T[i, j] = t
        idx += 1

    return n_S, n_C, n_L, n_K, n_D, B, cars, rate, orders_raw, T


# Load and parse

INSTANCE = "instance01.txt" 

n_S, n_C, n_L, n_K, n_D, B, cars, rate, orders_raw, T = load_instance(INSTANCE)

orders = {}
for k, (lvl, ps, rs, pt, rt) in orders_raw.items():
    orders[k] = (lvl, ps, rs, parse_time(pt), parse_time(rt))

K = list(orders.keys())
C = list(cars.keys())

R = {}
for k in K:
    lvl, _, _, pt, rt = orders[k]
    R[k] = rate[lvl] * hours_between(pt, rt)

planning_start = datetime(2023, 1, 1, 0, 0)


# Feasible arcs between orders
# Arc (i→j) exists if a car finishing order i can be cleaned and driven
# to order j's pickup station at least 30 min before pickup.

feasible_arcs = []
for i in K:
    for j in K:
        if i == j:
            continue
        _, _, rs_i, _, rt_i = orders[i]
        _, ps_j, _, pt_j, _ = orders[j]
        ready   = rt_i + timedelta(hours=4)                 # +1h late buffer + 3h cleaning
        arrival = ready + timedelta(minutes=T[rs_i, ps_j])
        if arrival <= pt_j - timedelta(minutes=30):
            feasible_arcs.append((i, j))

arcs_in  = {k: [] for k in K}
arcs_out = {k: [] for k in K}
for (i, j) in feasible_arcs:
    arcs_out[i].append(j)
    arcs_in[j].append(i)


# Model

m = Model("car_rental")

x      = m.addVars(K,               vtype=GRB.BINARY, name="accept")  # 1 if order k is accepted
assign = m.addVars(C, K,            vtype=GRB.BINARY, name="assign")  # 1 if car c serves order k
start  = m.addVars(C, K,            vtype=GRB.BINARY, name="start")   # 1 if order k is FIRST for car c
end_v  = m.addVars(C, K,            vtype=GRB.BINARY, name="end")     # 1 if order k is LAST  for car c
z      = m.addVars(C, feasible_arcs, vtype=GRB.BINARY, name="z")      # 1 if car c travels arc (i→j)


# Constraints

# (1) Initial reachability: car c can only start on order k if it can reach
#     k's pickup station from its initial station on time.
#
#     The problem states all cars are ready "right away" at planning_start,
#     so the 30-min readiness deadline is floored at planning_start — it
#     must never go negative (e.g. for orders whose pickup IS at t=0).
for c in C:
    for k in K:
        ps_k, pt_k = orders[k][1], orders[k][3]
        arrival  = planning_start + timedelta(minutes=T[cars[c][1], ps_k])
        deadline = max(planning_start, pt_k - timedelta(minutes=30))
        if arrival > deadline:
            m.addConstr(start[c, k] == 0)

# (2) Each accepted order is served by exactly one car.
for k in K:
    m.addConstr(quicksum(assign[c, k] for c in C) == x[k])

# (3) Level compatibility: car level must equal order level or order level + 1 (upgrade).
for c in C:
    cl = cars[c][0]
    for k in K:
        ol = orders[k][0]
        if not (cl == ol or cl == ol + 1):
            m.addConstr(assign[c, k] == 0)

# (4) start and end_v imply assignment.
for c in C:
    for k in K:
        m.addConstr(start[c, k] <= assign[c, k])
        m.addConstr(end_v[c, k] <= assign[c, k])

# (5) Each car has at most one first order and at most one last order.
for c in C:
    m.addConstr(quicksum(start[c, k] for k in K) <= 1)
    m.addConstr(quicksum(end_v[c, k] for k in K) <= 1)

# (6) Flow conservation: incoming: order k is either the first in the chain
#     or reached via a feasible arc from a previous order.
for c in C:
    for k in K:
        incoming = quicksum(z[c, i, k] for i in arcs_in[k])
        m.addConstr(start[c, k] + incoming == assign[c, k])

# (7) Flow conservation: outgoing: order k is either the last in the chain
#     or leads to a next order via a feasible arc.
for c in C:
    for k in K:
        outgoing = quicksum(z[c, k, j] for j in arcs_out[k])
        m.addConstr(end_v[c, k] + outgoing == assign[c, k])

# (8) Chain balance: number of chain-starts equals number of chain-ends per car.
for c in C:
    m.addConstr(
        quicksum(start[c, k] for k in K) ==
        quicksum(end_v[c, k] for k in K)
    )

# (9) Arc flow limits: at most one arc per direction per node per car.
for c in C:
    for k in K:
        m.addConstr(quicksum(z[c, i, k] for i in arcs_in[k])  <= 1)
        m.addConstr(quicksum(z[c, k, j] for j in arcs_out[k]) <= 1)

# (10) Relocation budget: total employee driving time cannot exceed B minutes.
travel_between = quicksum(
    T[orders[i][2], orders[j][1]] * z[c, i, j]
    for c in C for (i, j) in feasible_arcs
)
initial_travel = quicksum(
    T[cars[c][1], orders[k][1]] * start[c, k]
    for c in C for k in K
)
m.addConstr(travel_between + initial_travel <= B)


# Objective
# Maximize: revenue from accepted orders − compensation for rejected orders

profit = quicksum(R[k] * x[k] - 2 * R[k] * (1 - x[k]) for k in K)
m.setObjective(profit, GRB.MAXIMIZE)


# Solve

m.optimize()


# Output

if m.Status == GRB.OPTIMAL:
    accepted = [k for k in K if x[k].X > 0.5]
    rejected  = [k for k in K if x[k].X < 0.5]

    total_revenue      = sum(R[k] for k in accepted)
    total_compensation = sum(2 * R[k] for k in rejected)
    total_profit       = total_revenue - total_compensation

    print("\n" + "=" * 60)
    print("OPTIMAL PLAN".center(60))
    print("=" * 60)
    print(f"\n{'Total profit:':35} ${total_profit:>10,.0f}")
    print(f"{'Revenue (accepted orders):':35} ${total_revenue:>10,.0f}")
    print(f"{'Compensation (rejected orders):':35} ${total_compensation:>10,.0f}")
    print(f"\n{'Accepted orders:':35} {accepted}")
    print(f"{'Rejected orders:':35} {rejected}")

    print("\n" + "-" * 60)
    print("CAR ASSIGNMENT DETAILS")
    print("-" * 60)

    total_travel = 0

    for c in C:
        served = [k for k in K if assign[c, k].X > 0.5]
        if not served:
            print(f"\nCar {c} (Level {cars[c][0]}, starts at Station {cars[c][1]}): idle")
            continue

        # Reconstruct the ordered chain for this car
        first = [k for k in served if start[c, k].X > 0.5]
        chain = list(first)
        while True:
            cur = chain[-1]
            nxt = [j for j in arcs_out[cur] if z[c, cur, j].X > 0.5]
            if not nxt:
                break
            chain.append(nxt[0])

        cl = cars[c][0]
        print(f"\nCar {c} (Level {cl}, starts at Station {cars[c][1]}):")

        # Initial relocation if needed
        if chain:
            first_k  = chain[0]
            init_st  = cars[c][1]
            ps_first = orders[first_k][1]
            if init_st != ps_first:
                mins = T[init_st, ps_first]
                total_travel += mins
                print(f"  → Relocate Station {init_st} → Station {ps_first} ({mins} min)")

        for idx_c, k in enumerate(chain):
            lvl_k, ps_k, rs_k, pt_k, rt_k = orders[k]
            upgrade = " [UPGRADE]" if cl == lvl_k + 1 else ""
            print(f"  Order {k:>2}{upgrade}: "
                  f"pick up Station {ps_k} @ {pt_k.strftime('%Y/%m/%d %H:%M')}, "
                  f"return Station {rs_k} @ {rt_k.strftime('%Y/%m/%d %H:%M')}  "
                  f"[revenue: ${R[k]:,.0f}]")

            # Inter-order relocation
            if idx_c < len(chain) - 1:
                nxt_k    = chain[idx_c + 1]
                ps_next  = orders[nxt_k][1]
                if rs_k != ps_next:
                    mins       = T[rs_k, ps_next]
                    total_travel += mins
                    depart     = rt_k + timedelta(hours=4)
                    arrive     = depart + timedelta(minutes=mins)
                    print(f"       → Relocate Station {rs_k} → Station {ps_next} "
                          f"(depart {depart.strftime('%m/%d %H:%M')}, "
                          f"arrive {arrive.strftime('%m/%d %H:%M')}, {mins} min)")

    print(f"\n{'Total relocation time used:':35} {total_travel} / {B} min")
    print("=" * 60)

else:
    print(f"Solver status: {m.Status} — no optimal solution found.")

