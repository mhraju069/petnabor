"""Celery tasks for live app events (see api/core/realtime.py)."""

from celery import shared_task


@shared_task(ignore_result=True)
def send_broadcast_event(kind: str) -> None:
    """The batched broadcast at the end of a window (realtime.broadcast)."""
    from django.core.cache import cache

    from api.core.realtime import send_broadcast_now

    cache.delete(f"rt:pending:{kind}")
    send_broadcast_now(kind)
