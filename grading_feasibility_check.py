'''
You do not need to change the code in this file.
You only need to ensure that the TAs can run your algorithm here.
'''
from MTP_lib import *
from algorithm_module import heuristic_algorithm

def check_format(assignment, relocation):
    for i in assignment:
        if not isinstance(i, int):
            return False

    for relocate in relocation:
        if not isinstance(relocate[0], int) and not isinstance(relocate[1], int) and not isinstance(relocate[2], int):
            return False

        date_format = "%Y/%m/%d %H:%M"

        try:
            dateObject = datetime.strptime(relocate[3], date_format)
        except ValueError:
            return False

        import re
        r = re.compile('.{4}/.{2}/.{2} .{2}:.{2}')
        if r.match(relocate[3]) is None:
            return False

    return True
def get_valid_line(lines, idx, expected_len):
    while idx < len(lines):
        parts = lines[idx].split(",")
        if len(parts) == expected_len and parts[0].isdigit():
            return idx
        idx += 1
    return idx

def compute_profit(file_path, assignment):
    from datetime import datetime

    # ---- Parse file ----
    with open(file_path, "r") as f:
        raw_lines = [l.strip() for l in f if l.strip()]

    idx = 0
    idx = get_valid_line(raw_lines, idx, 6)
    n_S, n_C, n_L, n_K, n_D, B = map(int, raw_lines[idx].split(","))
    idx += 1

    idx = get_valid_line(raw_lines, idx, 2)
    rate = {}
    for _ in range(n_L):
        lvl, r = map(int, raw_lines[idx].split(","))
        rate[lvl] = r
        idx += 1

    idx = get_valid_line(raw_lines, idx, 6)
    orders = {}
    for _ in range(n_K):
        parts = raw_lines[idx].split(",")
        k = int(parts[0])
        orders[k] = (
            int(parts[1]),
            int(parts[2]),
            int(parts[3]),
            datetime.strptime(parts[4], "%Y/%m/%d %H:%M"),
            datetime.strptime(parts[5], "%Y/%m/%d %H:%M"),
        )
        idx += 1

    # ---- Revenue ----
    def hours_between(t1, t2):
        return (t2 - t1).total_seconds() / 3600

    R = {k: rate[orders[k][0]] * hours_between(orders[k][3], orders[k][4]) for k in orders}

    accepted = [k for k in range(1, n_K + 1) if assignment[k - 1] != -1]
    rejected = [k for k in range(1, n_K + 1) if assignment[k - 1] == -1]

    total_revenue = sum(R[k] for k in accepted)
    total_comp = sum(2 * R[k] for k in rejected)
    total_profit = total_revenue - total_comp

    return total_profit, total_revenue

if __name__ == '__main__':

    # read all instances (.txt file) under data folder
    all_data_list = os.listdir('data')

    # evaluate all instances
    result_df = pd.DataFrame(columns = ['Data name', 'Time', 'Profit', 'Feasibility'])

    for file_name in all_data_list:

        start_time = t.time()
        profit = np.nan
        revenue = np.nan
        feasibility = False

        try:
            '''
            1. We will import your algorithm here and give you file_path (e.g.,'data/instance01.txt') as the function argument.
            2. You need to return two lists "assignment" and "relocation".
            '''
            file_path = 'data/' + file_name
            assignment, relocation = heuristic_algorithm(file_path)

        except:
            print("the algorithm has errors")

        end_time = t.time()
        spent_time = end_time - start_time

        try:
            '''
            We will check the format, feasibility, and calculate the objective values here.
            '''
            
            if check_format(assignment, relocation):
                feasibility = True
                profit, revenue = compute_profit(file_path, assignment)

                print(f"\nFile: {file_name}")
                print(f"Revenue: {revenue:.0f}")
                print(f"Profit: {profit:.0f}")

            else:
                print("the format has errors")
                feasibility = False

            # feasibility, profit = find_obj_value(file_path, assignment, relocation)
        except:
            print("the algorithm has errors")


        result_df = pd.concat([result_df, pd.DataFrame([{
                'Data name': file_name,
                'Time': spent_time,
                'Profit': profit,
                'Revenue': revenue,
                'Feasibility': feasibility
            }])], ignore_index=True)

# output result
result_df.to_csv('result.csv', index = False)
