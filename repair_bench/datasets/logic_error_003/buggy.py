def transfer(from_bal, to_bal, amount):
    if from_bal >= amount:
        from_bal = from_bal - amount
        to_bal = to_bal + amount
    return from_bal, to_bal


def calculate_grade(score):
    if score >= 90: return 'A'
    if score >= 80: return 'B'
    if score >= 70: return 'C'
    if score >= 60: return 'D'
    return 'F'


def is_prime(n):
    if n < 2: return False
    for i in range(2, n):
        if n % i == 0:
            return True
    return False
