"""Prometheus metrics (review M3)."""
from __future__ import annotations

from prometheus_client import Counter, Histogram

REQUESTS = Counter("http_requests_total", "Total HTTP requests", ["method", "status"])
LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency (s)", ["method"])
