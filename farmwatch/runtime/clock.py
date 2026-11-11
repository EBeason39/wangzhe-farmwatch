"""可取消的等待；保留原业务等待时长。"""
import time as _time


class StopRequested(KeyboardInterrupt):
    pass


class Clock:
    def __init__(self, should_stop=None):
        self.should_stop = should_stop or (lambda: False)
        self.on_tick = lambda: None

    def sleep(self, seconds):
        deadline = _time.monotonic() + max(0, seconds)
        while True:
            if self.should_stop():
                raise StopRequested()
            self.on_tick()
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                return
            _time.sleep(min(remaining, 0.5))

    monotonic = staticmethod(_time.monotonic)
    time = staticmethod(_time.time)
    perf_counter = staticmethod(_time.perf_counter)
