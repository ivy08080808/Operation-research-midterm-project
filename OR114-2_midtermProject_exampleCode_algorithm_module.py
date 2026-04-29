from MTP_lib import *
def heuristic_algorithm(file_path):
    '''
    1. Write your heuristic algorithm here.
    2. We will call this function from grading_program.py to evaluate your algorithm.
    3. Do not change the function name and the file name.
    4. Do not import any extra library. Note that we use the latest version of Pandas (v2.0.0, released on April 3, 2023), and there are several updates which might affect your program implementation.
    5. The parameter is the file path of a data file, whose format is specified in the midterm project document.
    6. You need to return your plan in two lists "assignment" and "relocation".
        (a) "assignment" is a one-dimensional integer list with size n_K. 
            (i)   If you plan to satisfy order i, store the ID of the car (an integer) that will be used to satisfy order i in assignment[i - 1]. 
            (ii)  If you plan to reject order i, store -1 (an integer representing negative one) in assignment[i - 1].
        (b) "relocation" is a two-dimensional list with size ncm * 4, where ncm denotes the number of car moving (may be different in different plans). For the kth moving: 
            (i)   Store the car ID (an integer) in relocation[k - 1][0]. 
            (ii)  Store the starting station ID (an integer) in relocation[k - 1][1]. 
            (iii) Store the ending station ID (an integer) in relocation[k - 1][2]. 
            (iv)  Store the relocation starting time as a string in the "YYYY/MM/DD hh:mm" format in relocation[k - 1][3].
    7. The only PY file that you need and are allowed to submit is this algorithm_module.py.
    '''

    # read data and store the information into your self-defined variables
    fp = open(file_path, 'r')
    # for a_row in fp:
    #    print(a_row) # a_row is a list
    # ...



    # start your algorithm here
    assignment = []
    relocation = []
    # ...



    return assignment, relocation
