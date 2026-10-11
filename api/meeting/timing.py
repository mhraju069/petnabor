"""
When a meeting happens, for validation.

`visit_date` + `visit_time` are the participants' *local* wall-clock time and
no timezone is stored, while the server runs in UTC. So the server can't know
the exact instant; it only rejects actions that are impossible in every
timezone (UTC-12 … UTC+14). The app enforces the exact rule with the device
clock (features/meetings/utils.ts).
"""

from datetime import datetime, timedelta, timezone as dt_timezone

from django.utils import timezone

# Earliest / latest UTC offsets in use.
_MAX_AHEAD = timedelta(hours=14)   # e.g. Kiribati, UTC+14
_MAX_BEHIND = timedelta(hours=12)  # e.g. Baker Island, UTC-12


def _wall_clock(meeting_date, meeting_time):
    return datetime.combine(meeting_date, meeting_time).replace(tzinfo=dt_timezone.utc)


def has_started_somewhere(meeting_date, meeting_time, now=None):
    """
    True once the meeting's start time has been reached in at least one
    timezone. False means it's still in the future everywhere.
    """
    now = now or timezone.now()
    return now >= _wall_clock(meeting_date, meeting_time) - _MAX_AHEAD


def is_past_everywhere(meeting_date, meeting_time, now=None):
    """True when the meeting's start time has passed in every timezone."""
    now = now or timezone.now()
    return now > _wall_clock(meeting_date, meeting_time) + _MAX_BEHIND


def format_start(meeting_date, meeting_time):
    return f"{meeting_date:%b} {meeting_date.day}, {meeting_time:%H:%M}"
