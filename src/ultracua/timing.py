"""Per-step latency instrumentation.

The whole point of Phase 0 is to *measure* where time goes (snapshot / TTFT /
generation / actuation), so the flow cache built in Phase 1 has a real baseline to beat.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class Span:
    name: str
    ms: float


@dataclass
class StepTrace:
    index: int
    spans: list[Span] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.spans.append(Span(name, (time.perf_counter() - t0) * 1000.0))

    def add(self, name: str, ms: float) -> None:
        self.spans.append(Span(name, ms))

    @property
    def total_ms(self) -> float:
        return sum(s.ms for s in self.spans)

    def render(self) -> str:
        parts = "  ".join(f"{s.name}={s.ms:.0f}ms" for s in self.spans)
        label = "nav" if self.index < 0 else f"step {self.index}"
        return f"{label}: {parts}  total={self.total_ms:.0f}ms"
