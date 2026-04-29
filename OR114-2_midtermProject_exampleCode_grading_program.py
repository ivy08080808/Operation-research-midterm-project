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

if __name__ == '__main__':

    # read all instances (.txt file) under data folder
    all_data_list = os.listdir('data')

    # evaluate all instances
    result_df = pd.DataFrame(columns = ['Data name', 'Time', 'Profit', 'Feasibility'])

    for file_name in all_data_list:

        start_time = t.time()
        profit = np.nan
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
            if not check_format(assignment, relocation):
                print("the format has errors")

            # feasibility, profit = find_obj_value(file_path, assignment, relocation)
        except:
            print("the algorithm has errors")


        result_df = pd.concat([result_df, pd.DataFrame([{'Data name': file_name,
                                      'Time': spent_time,
                                      'Profit': profit,
                                      'Feasibility': feasibility}])], ignore_index = True)

# output result
result_df.to_csv('result.csv', index = False)
