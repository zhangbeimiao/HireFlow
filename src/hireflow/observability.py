"""Request correlation, structured access logs, and Prometheus primitives."""

import json
import logging
from contextvars import ContextVar
from time import perf_counter
from uuid import uuid4

from prometheus_client import Counter, Gauge, Histogram

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

HTTP_REQUESTS = Counter(
    "hireflow_http_requests_total",
    "HTTP responses by method, route, and status.",
    ["method", "route", "status"],
)
HTTP_DURATION = Histogram(
    "hireflow_http_request_duration_seconds",
    "HTTP request latency by method and route.",
    ["method", "route"],
)
HTTP_IN_FLIGHT = Gauge("hireflow_http_requests_in_flight", "Requests currently in flight.")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for field in ("method", "path", "status_code", "duration_ms"):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("hireflow.access")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


access_logger = configure_logging()


def begin_request(provided_id: str | None) -> tuple[str, object, float]:
    request_id = provided_id or str(uuid4())
    token = request_id_var.set(request_id)
    HTTP_IN_FLIGHT.inc()
    return request_id, token, perf_counter()


def finish_request(
    *, request_id: str, token: object, method: str, path: str, status_code: int, started_at: float
) -> None:
    duration_seconds = perf_counter() - started_at
    HTTP_IN_FLIGHT.dec()
    HTTP_REQUESTS.labels(method=method, route=path, status=str(status_code)).inc()
    HTTP_DURATION.labels(method=method, route=path).observe(duration_seconds)
    access_logger.info(
        "request.completed",
        extra={
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": round(duration_seconds * 1000, 2),
        },
    )
    request_id_var.reset(token)
