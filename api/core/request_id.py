"""
A short id per request, to find "what happened to this user's request".

- Every request gets one (or keeps the X-Request-ID the client sent).
- It's returned in the X-Request-ID response header, and in 500 error
  bodies as "error_id" (api/users/exception_handler.py) — a user can quote
  it from a screenshot.
- Every log line written while handling the request carries it
  ([req=…], see LOGGING in config/settings.py), so in Grafana → Explore →
  Loki, searching for the id shows everything that request did.
"""

import contextvars
import logging
import re
import uuid

_request_id = contextvars.ContextVar("request_id", default="-")
# Accept a client-supplied id only if it looks like one (no log injection).
_VALID = re.compile(r"^[A-Za-z0-9._-]{6,64}$")


def get_request_id() -> str:
    return _request_id.get()


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _VALID.match(incoming) else uuid.uuid4().hex[:12]
        request.request_id = request_id
        token = _request_id.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            _request_id.reset(token)
        response["X-Request-ID"] = request_id
        return response


class RequestIdLogFilter(logging.Filter):
    """Adds %(request_id)s to every log record ("-" outside a request)."""

    def filter(self, record):
        record.request_id = _request_id.get()
        return True
