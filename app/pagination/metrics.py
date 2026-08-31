"""Métricas agregadas y acotadas de paginación por tipo de recurso."""

from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Any

_lock = Lock()
_metrics: dict[str, dict[str, float | int]] = defaultdict(dict)


def increment(resource: str, field: str, value: int = 1) -> None:
    with _lock:
        current = _metrics[resource].get(field, 0)
        _metrics[resource][field] = int(current) + value


def observe_page(
    resource: str,
    *,
    duration_ms: float,
    items: int,
    has_more: bool,
    include_total: bool,
    total_from_cursor: bool,
    page_number: int,
) -> None:
    with _lock:
        metric = _metrics[resource]
        metric["requests"] = int(metric.get("requests", 0)) + 1
        metric["items"] = int(metric.get("items", 0)) + items
        metric["has_more_pages"] = int(metric.get("has_more_pages", 0)) + int(
            has_more
        )
        metric["include_total_requests"] = int(
            metric.get("include_total_requests", 0)
        ) + int(include_total)
        metric["total_from_cursor"] = int(metric.get("total_from_cursor", 0)) + int(
            total_from_cursor
        )
        metric["continuation_requests"] = int(
            metric.get("continuation_requests", 0)
        ) + int(page_number > 1)
        metric["max_page_number"] = max(
            int(metric.get("max_page_number", 0)), page_number
        )
        metric["duration_ms_sum"] = round(
            float(metric.get("duration_ms_sum", 0.0)) + duration_ms, 3
        )
        metric["duration_ms_max"] = round(
            max(float(metric.get("duration_ms_max", 0.0)), duration_ms), 3
        )


def snapshot() -> dict[str, dict[str, Any]]:
    with _lock:
        result = {resource: dict(values) for resource, values in _metrics.items()}
    for values in result.values():
        requests = int(values.get("requests", 0))
        values["duration_ms_avg"] = (
            round(float(values.get("duration_ms_sum", 0.0)) / requests, 3)
            if requests
            else 0.0
        )
    return result


def reset_for_tests() -> None:
    with _lock:
        _metrics.clear()
