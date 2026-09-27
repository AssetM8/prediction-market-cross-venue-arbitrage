"""In-process metrics registry (counters, gauges, timestamps).

Deliberately tiny: the demo needs visibility, not a metrics backend. Values are exposed as
JSON by ``GET /metrics`` and in Prometheus text format by ``GET /metrics?format=prometheus``.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime
from typing import Any

LabelKey = tuple[tuple[str, str], ...]


def _labels(labels: dict[str, str] | None) -> LabelKey:
    return tuple(sorted((labels or {}).items()))


class MetricsRegistry:
    """Thread-safe counters/gauges keyed by name and label set."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[LabelKey, int]] = defaultdict(dict)
        self._gauges: dict[str, dict[LabelKey, float]] = defaultdict(dict)
        self._timestamps: dict[str, dict[LabelKey, datetime]] = defaultdict(dict)

    def inc(self, name: str, amount: int = 1, **labels: str) -> None:
        key = _labels(labels)
        with self._lock:
            self._counters[name][key] = self._counters[name].get(key, 0) + amount

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        with self._lock:
            self._gauges[name][_labels(labels)] = value

    def mark(self, name: str, when: datetime, **labels: str) -> None:
        with self._lock:
            self._timestamps[name][_labels(labels)] = when

    def counter(self, name: str, **labels: str) -> int:
        with self._lock:
            return self._counters[name].get(_labels(labels), 0)

    def timestamp(self, name: str, **labels: str) -> datetime | None:
        with self._lock:
            return self._timestamps[name].get(_labels(labels))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": {
                    name: [{"labels": dict(k), "value": v} for k, v in sorted(series.items())]
                    for name, series in sorted(self._counters.items())
                },
                "gauges": {
                    name: [{"labels": dict(k), "value": v} for k, v in sorted(series.items())]
                    for name, series in sorted(self._gauges.items())
                },
                "timestamps": {
                    name: [{"labels": dict(k), "value": v.isoformat()} for k, v in sorted(series.items())]
                    for name, series in sorted(self._timestamps.items())
                },
            }

    def prometheus_text(self) -> str:
        lines: list[str] = []
        snap = self.snapshot()
        for kind, prom_type in (("counters", "counter"), ("gauges", "gauge")):
            for name, series in snap[kind].items():
                metric = f"pm_arb_{name}"
                lines.append(f"# TYPE {metric} {prom_type}")
                for point in series:
                    label_text = ",".join(f'{k}="{v}"' for k, v in point["labels"].items())
                    suffix = f"{{{label_text}}}" if label_text else ""
                    lines.append(f"{metric}{suffix} {point['value']}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._timestamps.clear()


#: Process-wide registry shared by the API and CLI.
METRICS = MetricsRegistry()
