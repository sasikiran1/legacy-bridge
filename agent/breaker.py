"""Per-endpoint circuit breakers (ADR 0004 starting parameters).

Closed: calls flow. Open after 3 consecutive failures or >= 50% failures over the last 10 calls: calls are
refused, so the agent goes straight to the browser. After the open interval one probe is allowed (half-open):
success closes the breaker, failure reopens it with the interval doubled, up to 10 minutes.

A failed write to this API never mutates state (404/410/501/5xx/schema rejection), so the probe may be the
real call; ADR 0004's read-only-probe refinement is left for when a write could partially succeed.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class Breaker:
    consecutive_threshold: int = 3
    window: int = 10
    ratio_threshold: float = 0.5
    base_open_seconds: float = 30.0
    max_open_seconds: float = 600.0
    clock: Callable[[], float] = time.monotonic
    outcomes: deque[bool] = field(default_factory=lambda: deque(maxlen=10))
    consecutive_failures: int = 0
    state: BreakerState = BreakerState.CLOSED
    opened_at: float = 0.0
    open_seconds: float = 30.0
    transitions: list[tuple[float, BreakerState]] = field(default_factory=list)

    def _set(self, state: BreakerState) -> None:
        if state is not self.state:
            self.state = state
            self.transitions.append((self.clock(), state))

    def allow(self) -> bool:
        if self.state is BreakerState.OPEN and self.clock() - self.opened_at >= self.open_seconds:
            self._set(BreakerState.HALF_OPEN)
        return self.state is not BreakerState.OPEN

    def record(self, success: bool) -> None:
        self.outcomes.append(success)
        if success:
            self.consecutive_failures = 0
            if self.state is BreakerState.HALF_OPEN:
                self.open_seconds = self.base_open_seconds
            self._set(BreakerState.CLOSED)
            return
        self.consecutive_failures += 1
        if self.state is BreakerState.HALF_OPEN:
            self.open_seconds = min(self.max_open_seconds, self.open_seconds * 2)
            self._trip()
            return
        failures = self.outcomes.count(False)
        ratio_tripped = (
            len(self.outcomes) >= self.window and failures / len(self.outcomes) >= self.ratio_threshold
        )
        if self.consecutive_failures >= self.consecutive_threshold or ratio_tripped:
            self._trip()

    def _trip(self) -> None:
        self.opened_at = self.clock()
        self._set(BreakerState.OPEN)


class BreakerRegistry:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._breakers: dict[str, Breaker] = {}

    def get(self, operation_id: str) -> Breaker:
        if operation_id not in self._breakers:
            self._breakers[operation_id] = Breaker(clock=self._clock)
        return self._breakers[operation_id]

    def snapshot(self) -> dict[str, str]:
        return {op: b.state.value for op, b in self._breakers.items()}
