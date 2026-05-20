class StreamBuffer:
    def __init__(self):
        self._buffer = [b"hello", b"world"]
        self._position = 5

    def advance_position(self, buffer_index=0):
        """Move position forward in buffer. Bug: uses - instead of +."""
        pos_in_buffer = 3
        while pos_in_buffer + len(self._buffer[buffer_index]) < self._position:
            pos_in_buffer += 1
        return pos_in_buffer
