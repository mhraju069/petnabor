"""
Deactivating an account (a pause the user can undo).

While deactivated the user is hidden everywhere others look (feed, profile,
stories, search, nearby, map, suggestions, friend lists), can't be messaged
and gets no push notifications. Their data stays. Signing in again turns the
account back on (users/services.py login paths).

Deleting an account is permanent: see users/account_deletion.py.
"""

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


def deactivate_account(user) -> None:
    user.is_deactivated = True
    user.deactivated_at = timezone.now()
    user.save(update_fields=["is_deactivated", "deactivated_at"])
    logger.info("Account %s deactivated by its owner", user.id)


def reactivate_if_deactivated(user) -> bool:
    """On sign-in: turn a deactivated account back on. True if it was off."""
    if not getattr(user, "is_deactivated", False):
        return False
    user.is_deactivated = False
    user.deactivated_at = None
    user.save(update_fields=["is_deactivated", "deactivated_at"])
    logger.info("Account %s reactivated on sign-in", user.id)
    return True
