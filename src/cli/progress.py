"""Simple progress indicator for long CLI operations."""

import sys
import threading
import time


class Spinner:
    """Animated spinner for long-running operations.

    Usage:
        with Spinner('Analyzing...'):
            do_slow_thing()
    """

    CHARS = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'

    def __init__(self, message=''):
        self.message = message
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        sys.stderr.write('\r\033[K')
        sys.stderr.flush()

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            char = self.CHARS[i % len(self.CHARS)]
            sys.stderr.write('\r{} {}'.format(char, self.message))
            sys.stderr.flush()
            i += 1
            time.sleep(0.1)

    def update(self, message):
        self.message = message
