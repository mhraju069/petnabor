"""Scheduled tip jobs (config/celery.py beat schedule)."""

from celery import shared_task


@shared_task(ignore_result=True)
def expire_held_tips():
    """Refund tips nobody claimed in time; cancel abandoned unpaid ones."""
    from .services import expire_held_tips as run

    run()
