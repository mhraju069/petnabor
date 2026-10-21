"""Celery tasks for live app events (see api/core/realtime.py)."""

from celery import shared_task


@shared_task(ignore_result=True)
def send_broadcast_event(kind: str) -> None:
    """The batched broadcast at the end of a window (realtime.broadcast)."""
    from django.core.cache import cache

    from api.core.realtime import send_broadcast_now

    cache.delete(f"rt:pending:{kind}")
    send_broadcast_now(kind)


@shared_task(ignore_result=True)
def purge_old_notifications() -> int:
    """
    Data retention: delete in-app notifications older than
    NOTIFICATION_RETENTION_READ_DAYS (read) / _UNREAD_DAYS (unread).
    Deletes in batches so the table is never locked for long.
    Returns how many were deleted.
    """
    from datetime import timedelta

    from django.conf import settings
    from django.db.models import Q
    from django.utils import timezone

    from .models import Notifications

    now = timezone.now()
    expired = Q(is_read=True, created_at__lt=now - timedelta(days=settings.NOTIFICATION_RETENTION_READ_DAYS)) | Q(
        is_read=False, created_at__lt=now - timedelta(days=settings.NOTIFICATION_RETENTION_UNREAD_DAYS)
    )
    total = 0
    while True:
        batch = list(Notifications.objects.filter(expired).values_list("pk", flat=True)[:5000])
        if not batch:
            break
        deleted, _ = Notifications.objects.filter(pk__in=batch).delete()
        total += deleted
    return total
