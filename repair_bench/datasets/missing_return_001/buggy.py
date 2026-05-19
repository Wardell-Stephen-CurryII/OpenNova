def get_status(code):
    if code == 200:
        return 'OK'
    elif code == 404:
        return 'Not Found'
    elif code == 500:
        return 'Server Error'


def is_positive(x):
    if x > 0:
        return True
