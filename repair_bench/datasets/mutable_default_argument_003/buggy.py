class Tracker:
    def __init__(self, events=[]):
        self.events = events

    def record(self, event):
        self.events.append(event)
        return len(self.events)


    def clear(self):
        self.events = []
