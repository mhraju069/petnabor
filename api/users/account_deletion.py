"""
Account deletion (required by the App Store and Google Play).

DELETE /api/users/user/ permanently deletes the account and the user's
content: profile, pets, posts, stories, comments, friendships, meetings,
notifications and devices (cascade). Uploaded files are removed from
storage by django-cleanup as the rows are deleted. The Firebase login is
deleted too, so the same Google/Apple/phone identity starts fresh.

Kept, without a link to the person:
  - tips they sent or received (tipper/recipient become null) — payment
    records the other party and accounting rely on;
  - chat messages already delivered to other people (sender becomes null).

Deletion is refused while the user still has money in their own Stripe
account (an available or pending balance, or a payout in progress): they
must withdraw first, or it would be lost.

Tips still HELD for them (they never set up payouts, so they can't
withdraw them) don't block deletion: they're refunded to the people who
sent them first. If a refund fails, the deletion stops.
"""

import logging
from decimal import Decimal

from django.db import transaction
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)


class AccountDeletionBlocked(APIException):
    status_code = 409
    default_code = "account_deletion_blocked"
    default_detail = "Your account can't be deleted right now."


def _money_left(user) -> Decimal:
    from api.tip.models import TipWithdrawal, WithdrawalStatus
    from api.tip.services import get_full_balance_summary

    if TipWithdrawal.objects.filter(user=user, status=WithdrawalStatus.PENDING).exists():
        raise AccountDeletionBlocked(
            "You have a payout in progress. Please wait until it's paid, then try again."
        )
    summary = get_full_balance_summary(user)
    # Not held_amount: held tips are refunded to their senders on deletion.
    return summary.get("available_balance", Decimal("0")) + summary.get("pending_balance", Decimal("0"))


def delete_account(user) -> None:
    remaining = _money_left(user)
    if remaining > 0:
        raise AccountDeletionBlocked(
            f"You still have ${remaining:.2f} in tips. Please withdraw it before deleting your account."
        )

    from api.tip.services import RefundError, refund_held_tips_for

    try:
        refund_held_tips_for(user)
    except RefundError:
        raise AccountDeletionBlocked(
            "We couldn't return the tips waiting for you to their senders. Please try again later."
        )

    user_id, firebase_uid = user.id, user.firebase_uid
    with transaction.atomic():
        user.delete()

    if firebase_uid:
        try:
            from firebase_admin import auth as firebase_auth

            firebase_auth.delete_user(firebase_uid)
        except Exception:
            # The app account is gone either way; a stale Firebase login can
            # only re-create an empty new account.
            logger.exception("Failed to delete Firebase user for deleted account %s", user_id)

    logger.info("Account %s deleted by its owner", user_id)
