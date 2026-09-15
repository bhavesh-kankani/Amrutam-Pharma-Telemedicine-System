import json
import logging
import time
import uuid
import threading
from datetime import datetime, timezone

_request_context = threading.local()


def get_current_request_id():
    """Retrieve the current thread-local request ID if available."""
    return getattr(_request_context, "request_id", None)


class JSONFormatter(logging.Formatter):
    """
    Formats log records as single-line JSON objects for production log aggregators
    (ELK, Datadog, CloudWatch, Google Cloud Logging).
    """

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
            "process": record.process,
            "thread": record.thread,
        }

        # Inject distributed request ID
        request_id = getattr(record, "request_id", None) or get_current_request_id()
        if request_id:
            log_data["request_id"] = str(request_id)

        # Inject optional structured attributes
        for key in ("user_id", "path", "method", "status_code", "duration_ms"):
            if hasattr(record, key):
                log_data[key] = getattr(record, key)

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


class RequestTracingMiddleware:
    """
    Extracts or generates an X-Request-ID for every HTTP transaction,
    injecting it into thread-local storage and attaching it to outgoing responses.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Extract incoming correlation ID or generate a new UUID4
        request_id = (
            request.headers.get("X-Request-ID")
            or request.META.get("HTTP_X_REQUEST_ID")
            or uuid.uuid4().hex
        )
        request.request_id = request_id
        _request_context.request_id = request_id

        start_time = time.monotonic()
        response = self.get_response(request)
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        # Attach request ID and duration to response headers
        response["X-Request-ID"] = request_id
        response["X-Response-Time-MS"] = str(duration_ms)

        # Clear context
        _request_context.request_id = None
        return response
