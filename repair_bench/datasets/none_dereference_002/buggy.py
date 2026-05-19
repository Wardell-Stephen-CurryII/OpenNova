class Address:
    def __init__(self, city):
        self.city = city


class Person:
    def __init__(self, address=None):
        self.address = address


def get_city(person):
    return person.address.city
