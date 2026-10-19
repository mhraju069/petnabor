"""
Helpers for the tab-bar "new since you last looked" badges.

The app keeps, per tab, the server time of its last visit and asks
"how many since <that>?". Each endpoint answers {"count": n, "now": <server
time>}; the app stores `now` on its next visit, so device clocks never
matter. Counts are capped (the badge shows "99+").
"""

from datetime import timezone as dt_timezone

from django.utils import timezone
from django.utils.dateparse import parse_datetime

MAX_BADGE_COUNT = 100  # anything above 99 shows as "99+"


def parse_since(request):
    """The `since` query param as an aware datetime, or None (first visit / bad value)."""
    raw = (request.query_params.get("since") or "").strip().replace(" ", "+")
    if not raw:
        return None
    try:
        value = parse_datetime(raw)
    except ValueError:  # well-formed but impossible, e.g. month 13
        return None
    if value is None:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, dt_timezone.utc)
    return value


def capped_count(queryset) -> int:
    return queryset[:MAX_BADGE_COUNT].count()


def badge_response_data(count: int) -> dict:
    return {"count": count, "now": timezone.now().isoformat()}
