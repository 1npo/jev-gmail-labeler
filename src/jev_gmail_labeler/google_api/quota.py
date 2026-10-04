"""Token-bucket style pacing for Gmail API quota units."""

import time
from collections import deque
from collections.abc import Callable

COSTS = {
    'messages.get': 20,
    'messages.list': 5,
    'messages.modify': 5,
    'history.list': 2,
    'labels.list': 1,
    'labels.create': 5,
    'watch': 100,
    'stop': 100,
    'getProfile': 1,
}
WINDOW_SECONDS = 60.0


class QuotaLimiter:
    """Block until a call's quota units fit in a sliding 60-second budget."""

    def __init__(
        self,
        units_per_minute: int = 5000,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._budget = units_per_minute
        self._clock = clock
        self._sleep = sleep
        self._window: deque[tuple[float, int]] = deque()
        self._used = 0

    def _expire(self, now: float) -> None:
        while self._window and self._window[0][0] <= now - WINDOW_SECONDS:
            self._used -= self._window.popleft()[1]

    def consume(self, units: int) -> None:
        """Reserve ``units``, sleeping first if the budget is exhausted."""
        now = self._clock()
        self._expire(now)
        while self._window and self._used + units > self._budget:
            self._sleep(self._window[0][0] + WINDOW_SECONDS - now)
            now = self._clock()
            self._expire(now)
        self._window.append((now, units))
        self._used += units
