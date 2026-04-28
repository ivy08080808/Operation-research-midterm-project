from gurobipy import *
from datetime import *

# Helper functions
def parse_time(t):
    return datetime.strptime(t, "%Y/%m/%d %H:%M")

def hours_between(t1, t2):
    return int((t2 - t1).total_seconds() / 3600)

# Load instance
def load_instance(filename):
    with open(filename, "r") as f:
        raw_lines = [l.strip() for l in f if l.strip() and "====" not in l]

    idx = 0
    idx += 1  # skip header

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
        orders_raw[k] = (
            int(parts[1]),
            int(parts[2]),
            int(parts[3]),
            parts[4],
            parts[5]
        )
        idx += 1

    idx += 1  # skip travel header

    T = {}
    for _ in range(n_S * n_S):
        i, j, t = map(int, raw_lines[idx].split(","))
        T[i, j] = t
        idx += 1

    return cars, rate, orders_raw, T, B

# LOAD DATA
cars, rate, orders_raw, T, B = load_instance("instance05.txt")

orders = {}
for k, v in orders_raw.items():
    l, ps, rs, pt, rt = v
    orders[k] = (l, ps, rs, parse_time(pt), parse_time(rt))

K = list(orders.keys())
C = list(cars.keys())

# Revenue
R = {}
for k in K:
    dur = hours_between(orders[k][3], orders[k][4])
    R[k] = rate[orders[k][0]] * dur

# MODEL
m = Model("car_rental")

x = m.addVars(K, vtype=GRB.BINARY, name="accept")
assign = m.addVars(C, K, vtype=GRB.BINARY, name="assign")
start = m.addVars(C, K, vtype=GRB.BINARY, name="start")

# Feasible arcs
feasible_arcs = []
for i in K:
    for j in K:
        if i == j:
            continue

        _, _, rsi, _, rti = orders[i]
        _, psj, _, ptj, _ = orders[j]

        ready = rti + timedelta(hours=4)
        arrival = ready + timedelta(minutes=T[rsi, psj])

        if arrival <= ptj - timedelta(minutes=30):
            feasible_arcs.append((i, j))

feasible_set = set(feasible_arcs)

z = m.addVars(C, feasible_arcs, vtype=GRB.BINARY, name="z")

# Arc helpers
arcs_in = {k: [] for k in K}
arcs_out = {k: [] for k in K}

for (i, j) in feasible_arcs:
    arcs_out[i].append(j)
    arcs_in[j].append(i)

# Constraints

# Assignment
for k in K:
    m.addConstr(quicksum(assign[c,k] for c in C) == x[k])

# Level constraint
for c in C:
    for k in K:
        cl = cars[c][0]
        ol = orders[k][0]
        if not (cl == ol or cl == ol + 1):
            m.addConstr(assign[c,k] == 0)

# Start constraints
for c in C:
    m.addConstr(quicksum(start[c,k] for k in K) <= 1)

for c in C:
    for k in K:
        m.addConstr(start[c,k] <= assign[c,k])

# Flow linking
for c in C:
    for k in K:
        incoming = quicksum(z[c,i,k] for i in arcs_in[k])
        m.addConstr(start[c,k] + incoming == assign[c,k])
# Flow limits
for c in C:
    for k in K:
        incoming = quicksum(z[c,i,k] for i in arcs_in[k])
        outgoing = quicksum(z[c,k,j] for j in arcs_out[k])

        m.addConstr(incoming <= 1)
        m.addConstr(outgoing <= 1)

        m.addConstr(incoming <= assign[c,k])
        m.addConstr(outgoing <= assign[c,k])

# Path consistency
for c in C:
    m.addConstr(
        quicksum(z[c,i,j] for (i,j) in feasible_arcs)
        == quicksum(assign[c,k] for k in K)
         - quicksum(start[c,k] for k in K)
    )

# Relocation budget
travel_between = quicksum(
    T[orders[i][2], orders[j][1]] * z[c,i,j]
    for c in C for (i,j) in feasible_arcs
)

initial_travel = quicksum(
    T[cars[c][1], orders[k][1]] * start[c,k]
    for c in C for k in K
)

m.addConstr(travel_between + initial_travel <= B)

# Objective
profit = quicksum(R[k]*x[k] - 2*R[k]*(1-x[k]) for k in K)
m.setObjective(profit, GRB.MAXIMIZE)

# Solve
m.optimize()

# Output
print("\n" + "="*50)
print("FINAL RESULT".center(50))
print("="*50)

print(f"{'Profit:':25} {m.objVal:>10.2f}")

accepted = [k for k in K if x[k].x > 0.5]
rejected = [k for k in K if x[k].x < 0.5]

print(f"{'Accepted Orders:':25} {accepted}")
print(f"{'Rejected Orders:':25} {rejected}")
print(f"{'Total Revenue:':25} {sum(R[k] for k in accepted):>10}")
