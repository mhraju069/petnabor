"""
Tip System Services — all Stripe API interactions live here.

Views call these functions; they never call stripe directly.
"""

import logging
import stripe
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction

from .models import (
    TipSettings,
    StripeConnectAccount,
    Tip,
    TipStatus,
    TipWithdrawal,
    WithdrawalStatus,
)
from api.notifications.services import send_notification
from api.notifications.models import NotificationTypes

logger = logging.getLogger(__name__)


def _stripe():
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


# Stripe's wording when a connected account is gone or not reachable with our
# key (deleted, disconnected, or test vs live mix-up).
_ACCOUNT_GONE_MARKERS = (
    "no such account",
    "no such destination",
    "does not have access to account",
    "account does not exist",
)


def _account_unusable(exc) -> bool:
    """True only for "this connected account is gone / not ours" errors."""
    if getattr(exc, "code", None) == "account_invalid":
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _ACCOUNT_GONE_MARKERS)


def _mark_needs_reconnect(connect, exc) -> None:
    """
    Stripe can't use this account any more. Keep the row (it links the user
    to any money there) -- never delete it -- and stop sending money to it;
    the user re-onboards to replace it (create_connect_account).
    """
    logger.error(
        "Stripe Connect account %s for user %s is unusable -- marked needs_reconnect: %s",
        connect.stripe_account_id, connect.user_id, exc,
    )
    connect.needs_reconnect = True
    connect.is_charges_enabled = False
    connect.is_payouts_enabled = False
    connect.is_onboarding_complete = False
    connect.save(
        update_fields=[
            "needs_reconnect", "is_charges_enabled", "is_payouts_enabled",
            "is_onboarding_complete", "updated_at",
        ]
    )


# ──────────────────────────────────────────────────────────────────────────────
# Stripe Connect — Onboarding
# ──────────────────────────────────────────────────────────────────────────────


def create_connect_account(user):
    """
    Create a Stripe Express Connected Account for a user and generate
    an onboarding URL. Returns (connect_account, onboarding_url).

    If the user already has a connect account, returns the existing one
    plus a fresh account link URL.
    """
    s = _stripe()

    existing = None
    try:
        existing = user.stripe_connect_account
        try:
            onboarding_url = _get_account_link(existing.stripe_account_id)
            return existing, onboarding_url
        except (stripe.error.InvalidRequestError, stripe.error.PermissionError) as exc:
            if not _account_unusable(exc):
                raise
            # The user asked to (re)connect and Stripe says the old account is
            # gone: give them a new one below, on the same row.
            _mark_needs_reconnect(existing, exc)
    except StripeConnectAccount.DoesNotExist:
        pass

    email = (user.email or "").strip()
    business_url = getattr(settings, "BUSINESS_URL", "https://petnabor.com")

    # Stripe rejects empty email strings
    account_params = dict(
        type="express",
        business_type="individual",
        business_profile={
            "mcc": "7299",
            "url": business_url,
        },
        settings={
            "payouts": {
                "schedule": {
                    "interval": "manual",
                }
            }
        },
        capabilities={
            "card_payments": {"requested": True},
            "transfers": {"requested": True},
        },
        metadata={"user_id": str(user.id)},
    )
    if email:
        account_params["email"] = email

    account = s.Account.create(**account_params)

    if existing is not None:
        logger.warning(
            "Replacing unusable Stripe account %s with %s for user %s",
            existing.stripe_account_id, account.id, user.id,
        )
        existing.stripe_account_id = account.id
        existing.needs_reconnect = False
        existing.save(update_fields=["stripe_account_id", "needs_reconnect", "updated_at"])
        connect = existing
    else:
        connect = StripeConnectAccount.objects.create(
            user=user,
            stripe_account_id=account.id,
        )

    onboarding_url = _get_account_link(account.id)
    return connect, onboarding_url


def _get_account_link(account_id, return_url=None, refresh_url=None):
    """
    Generate a short-lived Stripe Account Link URL for onboarding.

    Stripe requires HTTPS URLs. We use the backend as a bridge which then
    redirects to the mobile app's deep link (petnabor://).
    """
    s = _stripe()
    domain = getattr(settings, "BACKEND_BASE_URL", "https://app.petnabor.com")
    link = s.AccountLink.create(
        account=account_id,
        refresh_url=refresh_url or f"{domain}/api/tip/onboard/bridge/?status=refresh",
        return_url=return_url or f"{domain}/api/tip/onboard/bridge/?status=return",
        type="account_onboarding",
    )
    return link.url


def refresh_onboarding_link(user):
    """
    Return a fresh onboarding URL for an existing (but incomplete) account.
    Raises StripeConnectAccount.DoesNotExist if no account yet.
    """
    connect = user.stripe_connect_account
    try:
        return _get_account_link(connect.stripe_account_id)
    except (stripe.error.InvalidRequestError, stripe.error.PermissionError) as exc:
        if not _account_unusable(exc):
            raise
        # Old account gone: onboard onto a fresh one (same row).
        return create_connect_account(user)[1]


def get_connect_account_status(user):
    """
    Fetch live status from Stripe and sync it to the DB.
    Returns the updated StripeConnectAccount instance.

    If the account just became charges-enabled (first time), automatically
    releases any HELD tips waiting for this user.
    """
    s = _stripe()
    try:
        connect = user.stripe_connect_account
    except StripeConnectAccount.DoesNotExist:
        return None

    was_charges_enabled = connect.is_charges_enabled

    try:
        account = s.Account.retrieve(connect.stripe_account_id)
    except (stripe.error.InvalidRequestError, stripe.error.PermissionError) as exc:
        if not _account_unusable(exc):
            raise
        _mark_needs_reconnect(connect, exc)
        return connect

    connect.needs_reconnect = False
    connect.is_charges_enabled = account.charges_enabled
    connect.is_payouts_enabled = account.payouts_enabled
    connect.is_onboarding_complete = (
        account.details_submitted and account.charges_enabled
    )
    connect.save(
        update_fields=[
            "needs_reconnect",
            "is_charges_enabled",
            "is_payouts_enabled",
            "is_onboarding_complete",
            "updated_at",
        ]
    )

    # Auto-release held tips when account becomes charges-enabled for the first time
    if not was_charges_enabled and connect.is_charges_enabled:
        logger.info(
            "Account %s just became charges-enabled via status poll — releasing held tips for user %s",
            connect.stripe_account_id, user.id,
        )
        try:
            release_held_tips(user)
        except Exception as exc:
            logger.error(
                "Error auto-releasing held tips for user %s after status poll: %s", user.id, str(exc)
            )

    return connect


_PAYOUT_INTERVAL_LABELS = {
    "manual": "Manual",
    "daily": "Daily",
    "weekly": "Weekly",
    "monthly": "Monthly",
}


def get_connect_account_details(user):
    """
    What the user's payout account looks like on Stripe, for the app's
    "Payout account" screen: email, country, bank (last 4 only) and what
    Stripe still needs. Read-only; status syncing stays in
    get_connect_account_status. Returns None when there is no account.
    """
    s = _stripe()
    try:
        connect = user.stripe_connect_account
    except StripeConnectAccount.DoesNotExist:
        return None

    details = {
        "stripe_account_id": connect.stripe_account_id,
        "needs_reconnect": connect.needs_reconnect,
        "is_onboarding_complete": connect.is_onboarding_complete,
        "is_charges_enabled": connect.is_charges_enabled,
        "is_payouts_enabled": connect.is_payouts_enabled,
        "is_fully_verified": connect.is_fully_verified,
        "connected_since": connect.created_at,
        "email": "",
        "country": "",
        "default_currency": "",
        "bank": None,
        "payout_schedule": "",
        "requirements_due": 0,
        "disabled_reason": "",
    }
    if connect.needs_reconnect:
        return details

    try:
        account = s.Account.retrieve(connect.stripe_account_id)
    except (stripe.error.InvalidRequestError, stripe.error.PermissionError) as exc:
        if not _account_unusable(exc):
            raise
        _mark_needs_reconnect(connect, exc)
        details["needs_reconnect"] = True
        return details

    details["email"] = account.get("email") or ""
    details["country"] = account.get("country") or ""
    details["default_currency"] = (account.get("default_currency") or "").upper()

    external = (account.get("external_accounts") or {}).get("data") or []
    bank = next((x for x in external if x.get("default_for_currency")), None) or (
        external[0] if external else None
    )
    if bank:
        is_card = bank.get("object") == "card"
        details["bank"] = {
            "type": "card" if is_card else "bank_account",
            "name": (bank.get("brand") if is_card else bank.get("bank_name")) or "",
            "last4": bank.get("last4") or "",
            "currency": (bank.get("currency") or "").upper(),
        }

    schedule = ((account.get("settings") or {}).get("payouts") or {}).get("schedule") or {}
    interval = schedule.get("interval") or ""
    details["payout_schedule"] = _PAYOUT_INTERVAL_LABELS.get(interval, interval.title())

    requirements = account.get("requirements") or {}
    details["requirements_due"] = len(requirements.get("currently_due") or []) + len(
        requirements.get("past_due") or []
    )
    details["disabled_reason"] = requirements.get("disabled_reason") or ""
    return details


def create_dashboard_link(user):
    """
    One-time link to the user's Stripe Express dashboard (bank, payouts,
    tax info). Only exists once onboarding is submitted.
    """
    s = _stripe()
    connect = user.stripe_connect_account  # caller checks existence
    link = s.Account.create_login_link(connect.stripe_account_id)
    return link.url


# ──────────────────────────────────────────────────────────────────────────────
# Tip — Payment Intent
# ──────────────────────────────────────────────────────────────────────────────


def calculate_commission(amount: Decimal, commission_percentage: Decimal):
    """
    Returns (commission_amount, recipient_amount) both as Decimal.
    Rounds half-up to 2 decimal places.
    """
    commission = (amount * commission_percentage / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    recipient = amount - commission
    return commission, recipient


def create_tip_payment_intent(
    tipper, recipient, amount: Decimal, meeting=None, note="", idempotency_key=None
):
    """
    Create a Stripe PaymentIntent for a tip.

    TWO FLOWS:
    1. Recipient HAS verified Connect account:
       → PaymentIntent with transfer_data + application_fee_amount
       → Money goes directly to recipient's account after payment
       → Tip status = PENDING

    2. Recipient does NOT have a verified Connect account:
       → PaymentIntent WITHOUT transfer_data (money held on platform)
       → Tip status = HELD
       → Recipient gets a push notification to connect their account
       → When they connect, release_held_tips() transfers the money automatically
    """
    s = _stripe()
    tip_settings = TipSettings.get_instance()

    commission_pct = tip_settings.commission_percentage
    commission_amount, recipient_amount = calculate_commission(amount, commission_pct)

    amount_cents = int(amount * 100)
    commission_cents = int(commission_amount * 100)

    connect = None
    has_verified_account = False
    try:
        connect = recipient.stripe_connect_account
        has_verified_account = connect.is_fully_verified
    except StripeConnectAccount.DoesNotExist:
        pass

    intent_kwargs = {
        "amount": amount_cents,
        "currency": "usd",
        "automatic_payment_methods": {"enabled": True},
        "metadata": {
            "tipper_id": str(tipper.id),
            "recipient_id": str(recipient.id),
            "meeting_id": str(meeting.id) if meeting else "",
        }
    }
    if idempotency_key:
        intent_kwargs["idempotency_key"] = f"tip_intent_{idempotency_key}"

    if has_verified_account:
        try:
            intent_kwargs["application_fee_amount"] = commission_cents
            intent_kwargs["transfer_data"] = {"destination": connect.stripe_account_id}
            intent_kwargs["metadata"]["flow"] = "direct"
            intent = s.PaymentIntent.create(**intent_kwargs)
            tip_status = TipStatus.PENDING
        except (stripe.error.InvalidRequestError, stripe.error.PermissionError) as exc:
            if not _account_unusable(exc):
                raise
            # Recipient's account is gone: hold the tip until they reconnect.
            _mark_needs_reconnect(connect, exc)
            has_verified_account = False

    if not has_verified_account:
        # If fallback happened, idempotency key stays the same for Stripe.
        # But we remove transfer_data.
        intent_kwargs.pop("application_fee_amount", None)
        intent_kwargs.pop("transfer_data", None)
        intent_kwargs["metadata"]["flow"] = "held"
        
        intent = s.PaymentIntent.create(**intent_kwargs)
        tip_status = TipStatus.HELD

    tip, created = Tip.objects.get_or_create(
        stripe_payment_intent_id=intent.id,
        defaults={
            "tipper": tipper,
            "recipient": recipient,
            "meeting": meeting,
            "amount": amount,
            "commission_percentage": commission_pct,
            "commission_amount": commission_amount,
            "recipient_amount": recipient_amount,
            "status": tip_status,
            "note": note,
            "currency": "usd",
        }
    )

    return tip, intent.client_secret


# ──────────────────────────────────────────────────────────────────────────────
# Hold & Release
# ──────────────────────────────────────────────────────────────────────────────


def transfer_held_tip(tip, connect):
    """
    Transfer a single held tip to the recipient's Connect account.

    When a recipient connects their Stripe account, we create a Stripe Transfer
    using the original charge as source_transaction. The platform keeps the
    commission (difference between charge amount and transfer amount).
    """
    s = _stripe()

    if not tip.stripe_charge_id:
        logger.warning(
            "Cannot release held tip %s — no charge_id yet (payment not confirmed)", tip.id
        )
        return False

    recipient_cents = int(tip.recipient_amount * 100)

    try:
        transfer = s.Transfer.create(
            amount=recipient_cents,
            currency="usd",
            destination=connect.stripe_account_id,
            source_transaction=tip.stripe_charge_id,
            metadata={"tip_id": str(tip.id), "tipper_id": str(tip.tipper_id)},
        )
    except Exception as exc:
        logger.error("Stripe transfer failed for held tip %s: %s", tip.id, str(exc))
        return False

    with transaction.atomic():
        tip.status = TipStatus.SUCCEEDED
        tip.stripe_transfer_id = transfer.id
        tip.save(update_fields=["status", "stripe_transfer_id", "updated_at"])

    logger.info(
        "Released held tip %s → Transfer %s to %s",
        tip.id, transfer.id, connect.stripe_account_id,
    )

    tipper_name = tip.tipper.first_name if tip.tipper else "Someone"
    recipient_name = tip.recipient.first_name if tip.recipient else "the recipient"
    try:
        send_notification(
            title="Your tip money has arrived!",
            body=(
                f"${tip.recipient_amount:.2f} from {tipper_name} has been transferred to your "
                "bank account. It may take 1-2 business days to appear."
            ),
            user_id=tip.recipient_id,
            notification_type=NotificationTypes.TIP_RECEIVED,
            actor=tip.tipper,
            data={"tip_id": str(tip.id), "amount": str(tip.recipient_amount)},
        )
    except Exception as exc:
        logger.warning("Notification failed (recipient) after tip release %s: %s", tip.id, exc)
    try:
        send_notification(
            title="Tip delivered to recipient!",
            body=(
                f"Your ${tip.amount:.2f} tip to {recipient_name} has been released "
                "to their bank account."
            ),
            user_id=tip.tipper_id,
            notification_type=NotificationTypes.TIP_SENT,
            actor=tip.recipient,
            data={"tip_id": str(tip.id), "status": "released"},
        )
    except Exception as exc:
        logger.warning("Notification failed (tipper) after tip release %s: %s", tip.id, exc)

    return True


def release_held_tips(user):
    """
    Release all held tips for a user who has just connected their Stripe account.
    Called from handle_account_updated when the account becomes charges-enabled.

    Only releases tips where payment has already been confirmed (charge_id exists).
    Tips that are still PENDING payment will be handled by handle_payment_intent_succeeded.
    """
    try:
        connect = user.stripe_connect_account
    except StripeConnectAccount.DoesNotExist:
        return

    if not (connect.is_charges_enabled and connect.is_onboarding_complete):
        logger.info(
            "Skipping release for user %s — account not ready (charges=%s onboarding=%s)",
            user.id, connect.is_charges_enabled, connect.is_onboarding_complete,
        )
        return

    held_tips = Tip.objects.filter(
        recipient=user,
        status=TipStatus.HELD,
        stripe_charge_id__isnull=False,
    ).select_related("tipper", "recipient")

    if not held_tips.exists():
        logger.info("No held tips to release for user %s", user.id)
        return

    logger.info(
        "Releasing %d held tip(s) for user %s (account: %s)",
        held_tips.count(), user.id, connect.stripe_account_id,
    )

    for tip in held_tips:
        try:
            transfer_held_tip(tip, connect)
        except Exception as exc:
            logger.error(
                "Failed to release held tip %s for user %s: %s",
                tip.id, user.id, str(exc),
            )


# ──────────────────────────────────────────────────────────────────────────────
# Webhook Handlers
# ──────────────────────────────────────────────────────────────────────────────


def handle_payment_intent_succeeded(payment_intent):
    """Mark tip as SUCCEEDED. HELD tips only get charge_id saved; transfer happens when account connects."""
    pi_id = payment_intent["id"]

    charge_id = payment_intent.get("latest_charge")
    if not charge_id:
        charges_data = payment_intent.get("charges", {}).get("data", [])
        if charges_data:
            charge_id = charges_data[0].get("id")

    with transaction.atomic():
        try:
            tip = Tip.objects.select_for_update().get(stripe_payment_intent_id=pi_id)
        except Tip.DoesNotExist:
            logger.warning("Webhook: Tip not found for payment_intent %s", pi_id)
            return

        if tip.status in (
            TipStatus.SUCCEEDED, TipStatus.FAILED, TipStatus.REFUNDED,
            TipStatus.CANCELLED, TipStatus.DISPUTED,
        ):
            logger.info("Webhook: Tip %s already %s — skipping", tip.id, tip.status)
            return

        if tip.status == TipStatus.HELD:
            tip.stripe_charge_id = charge_id
            tip.save(update_fields=["stripe_charge_id", "updated_at"])
            logger.info("Held tip %s payment confirmed (%s) — awaiting recipient account", tip.id, charge_id)

            tipper_name = tip.tipper.first_name or "Someone"

            try:
                send_notification(
                    title="You have a tip waiting!",
                    # The recipient's share (after the platform fee), as in
                    # every other recipient notification.
                    body=(
                        f"{tipper_name} sent you a ${tip.recipient_amount:.2f} tip. "
                        "Connect your bank account to receive it."
                    ),
                    user_id=tip.recipient_id,
                    notification_type=NotificationTypes.TIP_ENABLE_REQUEST,
                    actor=tip.tipper,
                    data={"action": "connect_stripe", "tip_id": str(tip.id), "amount": str(tip.recipient_amount)},
                )
            except Exception as exc:
                logger.warning("Notification failed for held tip %s (recipient): %s", tip.id, exc)

            try:
                recipient_name = tip.recipient.first_name or "the recipient"
                send_notification(
                    title="Tip sent successfully!",
                    body=(
                        f"Your ${tip.amount:.2f} tip to {recipient_name} was received. "
                        "It will be released once they connect their bank account."
                    ),
                    user_id=tip.tipper_id,
                    notification_type=NotificationTypes.TIP_SENT,
                    actor=tip.recipient,
                    data={"tip_id": str(tip.id), "status": "held"},
                )
            except Exception as exc:
                logger.warning("Notification failed for held tip %s (tipper): %s", tip.id, exc)

            # Attempt immediate release in case the recipient JUST connected their account
            # (race condition: account.updated webhook arrived before payment_intent.succeeded)
            try:
                from .models import StripeConnectAccount
                connect_acct = StripeConnectAccount.objects.filter(
                    user_id=tip.recipient_id,
                    is_charges_enabled=True,
                    is_onboarding_complete=True,
                ).first()
                if connect_acct:
                    logger.info(
                        "Held tip %s: recipient already has a verified account — releasing immediately",
                        tip.id,
                    )
                    transfer_held_tip(tip, connect_acct)
            except Exception as exc:
                logger.warning("Immediate-release attempt failed for held tip %s: %s", tip.id, exc)

            return

        # PENDING → SUCCEEDED (direct transfer flow)
        tip.status = TipStatus.SUCCEEDED
        tip.stripe_charge_id = charge_id
        tip.save(update_fields=["status", "stripe_charge_id", "updated_at"])
        logger.info("Tip %s SUCCEEDED (PI: %s, charge: %s)", tip.id, pi_id, charge_id)

    tipper_name = tip.tipper.first_name or "Someone"
    recipient_name = tip.recipient.first_name or "the recipient"
    try:
        send_notification(
            title="You received a tip!",
            body=f"{tipper_name} sent you a ${tip.recipient_amount:.2f} tip. It's now in your account!",
            user_id=tip.recipient_id,
            notification_type=NotificationTypes.TIP_RECEIVED,
            actor=tip.tipper,
            data={"tip_id": str(tip.id), "amount": str(tip.recipient_amount)},
        )
    except Exception as exc:
        logger.warning("Notification failed (recipient) after tip succeeded (PI: %s): %s", pi_id, exc)
    try:
        send_notification(
            title="Tip sent!",
            body=f"Your ${tip.amount:.2f} tip to {recipient_name} was delivered successfully.",
            user_id=tip.tipper_id,
            notification_type=NotificationTypes.TIP_SENT,
            actor=tip.recipient,
            data={"tip_id": str(tip.id), "status": "succeeded"},
        )
    except Exception as exc:
        logger.warning("Notification failed (tipper) after tip succeeded (PI: %s): %s", pi_id, exc)


def handle_payment_intent_failed(payment_intent):
    """Mark tip as FAILED and notify the tipper."""
    pi_id = payment_intent["id"]
    failure_msg = (payment_intent.get("last_payment_error", {}) or {}).get("message", "Payment failed.")

    with transaction.atomic():
        try:
            tip = Tip.objects.select_for_update().get(stripe_payment_intent_id=pi_id)
        except Tip.DoesNotExist:
            logger.warning("Webhook: Tip not found for failed payment_intent %s", pi_id)
            return

        if tip.status in (
            TipStatus.SUCCEEDED, TipStatus.FAILED, TipStatus.REFUNDED,
            TipStatus.CANCELLED, TipStatus.DISPUTED,
        ):
            logger.info("Webhook: Tip %s already %s — skipping", tip.id, tip.status)
            return

        tip.status = TipStatus.FAILED
        tip.save(update_fields=["status", "updated_at"])
        logger.info("Tip %s FAILED (PI: %s): %s", tip.id, pi_id, failure_msg)

    try:
        send_notification(
            title="Tip payment failed",
            body=(
                f"Your ${tip.amount:.2f} tip could not be processed. "
                "Please check your payment method and try again."
            ),
            user_id=tip.tipper_id,
            notification_type=NotificationTypes.TIP_SENT,
            data={"tip_id": str(tip.id), "status": "failed"},
        )
    except Exception as exc:
        logger.warning("Notification failed after tip failure (PI: %s): %s", pi_id, exc)


def handle_payment_intent_cancelled(payment_intent):
    """Mark the tip as cancelled."""
    pi_id = payment_intent["id"]

    with transaction.atomic():
        try:
            tip = Tip.objects.select_for_update().get(stripe_payment_intent_id=pi_id)
        except Tip.DoesNotExist:
            return

        # Only a tip still waiting for payment can be cancelled.
        if tip.status not in (TipStatus.PENDING, TipStatus.HELD):
            return
        if tip.status == TipStatus.HELD and tip.stripe_charge_id:
            return

        tip.status = TipStatus.CANCELLED
        tip.save(update_fields=["status", "updated_at"])


def handle_charge_refunded(charge):
    """Mark the tip as refunded when the charge is FULLY refunded."""
    charge_id = charge["id"]
    pi_id = charge.get("payment_intent")

    # charge.refunded also fires for partial refunds; those don't make the
    # whole tip "refunded".
    if charge.get("refunded") is False:
        logger.info(
            "Partial refund on charge %s (%s of %s cents) -- tip status unchanged",
            charge_id, charge.get("amount_refunded"), charge.get("amount"),
        )
        return

    with transaction.atomic():
        tip = None
        try:
            tip = Tip.objects.select_for_update().get(stripe_charge_id=charge_id)
        except Tip.DoesNotExist:
            if pi_id:
                try:
                    tip = Tip.objects.select_for_update().get(stripe_payment_intent_id=pi_id)
                except Tip.DoesNotExist:
                    pass

        if tip is None:
            logger.warning(
                "Webhook: Tip not found for refunded charge %s (PI: %s)", charge_id, pi_id
            )
            return

        if tip.status == TipStatus.REFUNDED:
            return  # Idempotent

        tip.status = TipStatus.REFUNDED
        tip.save(update_fields=["status", "updated_at"])
        logger.info("Tip %s refunded (charge: %s)", tip.id, charge_id)

    try:
        send_notification(
            title="Tip refunded",
            body=(
                f"Your ${tip.amount:.2f} tip has been refunded and will appear in your "
                "account within 5-10 business days."
            ),
            user_id=tip.tipper_id,
            notification_type=NotificationTypes.TIP_SENT,
            data={"tip_id": str(tip.id), "status": "refunded"},
        )
    except Exception as exc:
        logger.warning("Notification failed after refund (charge: %s): %s", charge_id, exc)


def handle_account_updated(account):
    """Sync Stripe Connect account status when account.updated fires.

    When an account becomes charges-enabled for the first time,
    automatically release any HELD tips waiting for this user.
    """
    account_id = account["id"]

    try:
        connect = StripeConnectAccount.objects.get(stripe_account_id=account_id)
    except StripeConnectAccount.DoesNotExist:
        logger.warning("Webhook: StripeConnectAccount not found for %s", account_id)
        return

    was_charges_enabled = connect.is_charges_enabled

    connect.needs_reconnect = False
    connect.is_charges_enabled = account.get("charges_enabled", False)
    connect.is_payouts_enabled = account.get("payouts_enabled", False)
    connect.is_onboarding_complete = (
        account.get("details_submitted", False) and connect.is_charges_enabled
    )
    connect.save(
        update_fields=[
            "needs_reconnect",
            "is_charges_enabled",
            "is_payouts_enabled",
            "is_onboarding_complete",
            "updated_at",
        ]
    )
    logger.info(
        "Connect account %s synced: charges=%s payouts=%s onboarding=%s",
        account_id,
        connect.is_charges_enabled,
        connect.is_payouts_enabled,
        connect.is_onboarding_complete,
    )

    if not was_charges_enabled and connect.is_charges_enabled:
        logger.info(
            "Account %s just became charges-enabled — releasing held tips for user %s",
            account_id, connect.user_id,
        )
        try:
            release_held_tips(connect.user)
        except Exception as exc:
            logger.error(
                "Error releasing held tips for user %s: %s", connect.user_id, str(exc)
            )


def handle_payout_paid(payout):
    """Mark a withdrawal as paid when the payout succeeds."""
    payout_id = payout["id"]

    with transaction.atomic():
        try:
            withdrawal = TipWithdrawal.objects.select_for_update().get(
                stripe_payout_id=payout_id
            )
        except TipWithdrawal.DoesNotExist:
            logger.warning("Webhook: TipWithdrawal not found for payout %s", payout_id)
            return

        withdrawal.status = WithdrawalStatus.PAID
        withdrawal.save(update_fields=["status", "updated_at"])
        logger.info("Withdrawal %s paid (payout: %s)", withdrawal.id, payout_id)


def handle_payout_failed(payout):
    """Mark a withdrawal as failed when the payout fails."""
    payout_id = payout["id"]

    with transaction.atomic():
        try:
            withdrawal = TipWithdrawal.objects.select_for_update().get(
                stripe_payout_id=payout_id
            )
        except TipWithdrawal.DoesNotExist:
            logger.warning(
                "Webhook: TipWithdrawal not found for failed payout %s", payout_id
            )
            return

        failure_msg = payout.get("failure_message") or payout.get(
            "failure_code", "Unknown failure"
        )
        withdrawal.status = WithdrawalStatus.FAILED
        withdrawal.failure_message = failure_msg
        withdrawal.save(update_fields=["status", "failure_message", "updated_at"])
        logger.info(
            "Withdrawal %s failed (payout: %s): %s",
            withdrawal.id,
            payout_id,
            failure_msg,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Refunds (admin action, expired held tips, account deletion)
# ──────────────────────────────────────────────────────────────────────────────


class RefundError(Exception):
    """A refund couldn't be done (wrong state, or Stripe refused)."""


def refund_tip(tip, *, reason="requested_by_customer", notify_body=None):
    """
    Give the tipper their money back -- and take it back from the recipient
    if they already got it, plus the platform fee, so the platform doesn't
    pay for it:

      - held, never paid:          cancel the PaymentIntent (nothing charged)
      - held, paid, not released:  refund (money is still on the platform)
      - held, then released:       reverse the transfer, then refund
      - direct (destination):      refund with reverse_transfer + fee refund

    The charge.refunded webhook arrives later and is a no-op by then.
    """
    s = _stripe()
    if tip.status not in (TipStatus.HELD, TipStatus.SUCCEEDED):
        raise RefundError(f"A {tip.get_status_display().lower()} tip can't be refunded.")

    try:
        if tip.status == TipStatus.HELD and not tip.stripe_charge_id:
            if tip.stripe_payment_intent_id:
                s.PaymentIntent.cancel(tip.stripe_payment_intent_id)
            new_status = TipStatus.CANCELLED
        else:
            if not tip.stripe_charge_id:
                raise RefundError("This tip has no charge to refund.")
            key = f"tip_refund_{tip.id}"
            if tip.status == TipStatus.HELD:
                s.Refund.create(charge=tip.stripe_charge_id, reason=reason, idempotency_key=key)
            elif tip.stripe_transfer_id:
                s.Transfer.create_reversal(
                    tip.stripe_transfer_id, idempotency_key=f"tip_reversal_{tip.id}"
                )
                s.Refund.create(charge=tip.stripe_charge_id, reason=reason, idempotency_key=key)
            else:
                s.Refund.create(
                    charge=tip.stripe_charge_id,
                    reason=reason,
                    reverse_transfer=True,
                    refund_application_fee=True,
                    idempotency_key=key,
                )
            new_status = TipStatus.REFUNDED
    except stripe.error.StripeError as exc:
        logger.error("Refund of tip %s failed: %s", tip.id, exc)
        raise RefundError(str(getattr(exc, "user_message", None) or exc)) from exc

    tip.status = new_status
    tip.save(update_fields=["status", "updated_at"])
    logger.info("Tip %s %s (%s)", tip.id, new_status, reason)

    if new_status == TipStatus.REFUNDED and tip.tipper_id:
        try:
            send_notification(
                title="Tip refunded",
                body=notify_body or (
                    f"Your ${tip.amount:.2f} tip has been refunded and will appear in your "
                    "account within 5-10 business days."
                ),
                user_id=tip.tipper_id,
                notification_type=NotificationTypes.TIP_SENT,
                actor=tip.recipient,
                data={"tip_id": str(tip.id), "status": "refunded"},
            )
        except Exception as exc:
            logger.warning("Notification failed after refunding tip %s: %s", tip.id, exc)
    return tip


def expire_held_tips(now=None):
    """
    Held tips don't wait forever for the recipient to connect payouts:
      - paid but unclaimed for HELD_TIP_EXPIRY_DAYS -> refunded to the tipper
      - never paid (payment sheet abandoned) for a day -> cancelled
    Returns (refunded, cancelled) counts. Daily Celery task.
    """
    from datetime import timedelta

    from django.utils import timezone

    now = now or timezone.now()
    days = getattr(settings, "HELD_TIP_EXPIRY_DAYS", 30)
    refunded = cancelled = 0

    unpaid = Tip.objects.filter(
        status=TipStatus.HELD, stripe_charge_id__isnull=True,
        created_at__lt=now - timedelta(days=1),
    )
    for tip in unpaid:
        try:
            refund_tip(tip, reason="requested_by_customer")
            cancelled += 1
        except RefundError as exc:
            logger.warning("Couldn't cancel abandoned held tip %s: %s", tip.id, exc)

    expired = Tip.objects.filter(
        status=TipStatus.HELD, stripe_charge_id__isnull=False,
        created_at__lt=now - timedelta(days=days),
    ).select_related("tipper", "recipient")
    for tip in expired:
        recipient_name = (tip.recipient.first_name if tip.recipient else "") or "The recipient"
        try:
            refund_tip(
                tip,
                notify_body=(
                    f"{recipient_name} didn't set up payouts within {days} days, so your "
                    f"${tip.amount:.2f} tip was refunded. It will appear in 5-10 business days."
                ),
            )
            refunded += 1
        except RefundError as exc:
            logger.error("Couldn't refund expired held tip %s: %s", tip.id, exc)
            continue
        if tip.recipient_id:
            try:
                send_notification(
                    title="A tip expired",
                    body=(
                        f"A ${tip.recipient_amount:.2f} tip waited {days} days for you to set up "
                        "payouts, so it was returned to the sender."
                    ),
                    user_id=tip.recipient_id,
                    notification_type=NotificationTypes.TIP_RECEIVED,
                    actor=tip.tipper,
                    data={"tip_id": str(tip.id), "status": "expired"},
                )
            except Exception as exc:
                logger.warning("Notification failed for expired tip %s: %s", tip.id, exc)

    if refunded or cancelled:
        logger.info("Held tips: %d refunded (expired), %d cancelled (unpaid)", refunded, cancelled)
    return refunded, cancelled


def refund_held_tips_for(user):
    """
    Before deleting an account: return every tip still held for this user
    to its sender. Raises RefundError if any can't be refunded (so the
    deletion stops rather than strand the money).
    """
    held = Tip.objects.filter(recipient=user, status=TipStatus.HELD).select_related("tipper")
    for tip in held:
        refund_tip(
            tip,
            notify_body=(
                f"The account your ${tip.amount:.2f} tip was waiting for was closed, so the "
                "tip was refunded. It will appear in 5-10 business days."
            ),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Disputes (chargebacks)
# ──────────────────────────────────────────────────────────────────────────────


def _tip_for_charge(charge_id, for_update=True):
    qs = Tip.objects.select_for_update() if for_update else Tip.objects
    return qs.filter(stripe_charge_id=charge_id).first()


def handle_dispute_created(dispute):
    """
    A tipper disputed the charge. With these charges the PLATFORM pays the
    chargeback, so pull the recipient's share back from their account
    (reversed if we win -- handle_dispute_closed).
    """
    s = _stripe()
    charge_id = dispute.get("charge")
    with transaction.atomic():
        tip = _tip_for_charge(charge_id)
        if tip is None:
            logger.warning("Dispute %s: no tip for charge %s", dispute.get("id"), charge_id)
            return
        if tip.status == TipStatus.DISPUTED:
            return  # already handled (webhook retry)

        tip.status_before_dispute = tip.status
        tip.stripe_dispute_id = dispute.get("id")
        tip.status = TipStatus.DISPUTED

        if tip.status_before_dispute == TipStatus.SUCCEEDED:
            # Where the recipient's money came from: our own transfer
            # (held, then released) or the charge's automatic transfer.
            transfer_id = tip.stripe_transfer_id
            try:
                if not transfer_id:
                    transfer_id = s.Charge.retrieve(charge_id).get("transfer")
                if transfer_id:
                    s.Transfer.create_reversal(
                        transfer_id,
                        amount=int(tip.recipient_amount * 100),
                        idempotency_key=f"tip_dispute_reversal_{tip.id}",
                    )
                    tip.funds_reversed = True
            except stripe.error.StripeError as exc:
                # e.g. the recipient already withdrew it: the platform covers it.
                logger.error(
                    "Dispute on tip %s: couldn't pull back the recipient's $%s: %s",
                    tip.id, tip.recipient_amount, exc,
                )

        tip.save(update_fields=[
            "status", "status_before_dispute", "stripe_dispute_id", "funds_reversed", "updated_at",
        ])
    logger.warning(
        "Tip %s DISPUTED (dispute %s, reason %s, recipient funds reversed: %s)",
        tip.id, dispute.get("id"), dispute.get("reason"), tip.funds_reversed,
    )


def handle_dispute_closed(dispute):
    """
    Dispute decided. Won: the recipient gets their share back. Lost: the
    tipper kept the money -- the tip ends as refunded.
    """
    s = _stripe()
    outcome = dispute.get("status")
    with transaction.atomic():
        tip = _tip_for_charge(dispute.get("charge"))
        if tip is None or tip.status != TipStatus.DISPUTED:
            return

        if outcome in ("won", "warning_closed"):
            if tip.funds_reversed and tip.recipient_id:
                connect = StripeConnectAccount.objects.filter(user_id=tip.recipient_id).first()
                if connect and not connect.needs_reconnect:
                    try:
                        s.Transfer.create(
                            amount=int(tip.recipient_amount * 100),
                            currency=tip.currency,
                            destination=connect.stripe_account_id,
                            metadata={"tip_id": str(tip.id), "reason": "dispute_won"},
                            idempotency_key=f"tip_dispute_won_{tip.id}",
                        )
                        tip.funds_reversed = False
                    except stripe.error.StripeError as exc:
                        logger.error("Dispute won but re-paying tip %s failed: %s", tip.id, exc)
            tip.status = tip.status_before_dispute or TipStatus.SUCCEEDED
        else:  # lost
            tip.status = TipStatus.REFUNDED

        tip.save(update_fields=["status", "funds_reversed", "updated_at"])
    logger.warning("Dispute on tip %s closed: %s -> %s", tip.id, outcome, tip.status)


# ──────────────────────────────────────────────────────────────────────────────
# Withdrawal (Payout)
# ──────────────────────────────────────────────────────────────────────────────


def get_stripe_balance(user):
    """
    Retrieve the available balance from the user's Stripe Connected Account.
    Returns the available USD balance in dollars (Decimal).
    """
    s = _stripe()
    try:
        connect = user.stripe_connect_account
    except StripeConnectAccount.DoesNotExist:
        return Decimal("0.00")

    if not connect.is_fully_verified:
        return Decimal("0.00")

    balance = s.Balance.retrieve(stripe_account=connect.stripe_account_id)

    available_usd = Decimal("0.00")
    for entry in balance.get("available", []):
        if entry["currency"] == "usd":
            available_usd = Decimal(str(entry["amount"])) / Decimal("100")
            break

    return available_usd


def get_full_balance_summary(user):
    """
    Returns balance summary: Stripe available/pending balances + DB-tracked held tips and earnings.
    PENDING tips (sender hasn't completed card payment) are excluded from the recipient's view.
    """
    from django.db.models import Sum

    result = {
        "available_balance": Decimal("0.00"),
        "pending_balance": Decimal("0.00"),
        "held_amount": Decimal("0.00"),
        "total_earned": Decimal("0.00"),
        "is_connected": False,
        "is_fully_verified": False,
        "currency": "usd",
    }

    earned = Tip.objects.filter(
        recipient=user, status=TipStatus.SUCCEEDED
    ).aggregate(total=Sum("recipient_amount"))["total"]
    result["total_earned"] = earned or Decimal("0.00")

    held = Tip.objects.filter(
        recipient=user,
        status=TipStatus.HELD,
        stripe_charge_id__isnull=False,
    ).aggregate(total=Sum("recipient_amount"))["total"]
    result["held_amount"] = held or Decimal("0.00")

    try:
        connect = user.stripe_connect_account
        result["is_connected"] = True
        result["is_fully_verified"] = connect.is_fully_verified
    except StripeConnectAccount.DoesNotExist:
        return result

    if not connect.is_fully_verified:
        return result

    s = _stripe()
    try:
        balance = s.Balance.retrieve(stripe_account=connect.stripe_account_id)
        for entry in balance.get("available", []):
            if entry["currency"] == "usd":
                result["available_balance"] = Decimal(str(entry["amount"])) / Decimal("100")
                break
        for entry in balance.get("pending", []):
            if entry["currency"] == "usd":
                result["pending_balance"] = Decimal(str(entry["amount"])) / Decimal("100")
                break
    except Exception as exc:
        logger.warning("Failed to fetch Stripe balance for user %s: %s", user.id, exc)

    return result


def create_withdrawal(user, amount: Decimal):
    """
    Create a Stripe Payout on the user's Connected Account.
    Returns the TipWithdrawal instance.

    Raises ValueError for validation failures (not verified, amount too low, etc.)
    """
    s = _stripe()
    tip_settings = TipSettings.get_instance()

    try:
        connect = user.stripe_connect_account
    except StripeConnectAccount.DoesNotExist:
        raise ValueError("You must connect your Stripe account before withdrawing.")

    if not connect.is_fully_verified:
        raise ValueError(
            "Your Stripe account is not fully verified. Please complete onboarding."
        )

    if amount < tip_settings.minimum_withdrawal_amount:
        raise ValueError(
            f"Minimum withdrawal amount is ${tip_settings.minimum_withdrawal_amount}."
        )

    available = get_stripe_balance(user)
    if amount > available:
        raise ValueError(
            f"Insufficient balance. Available: ${available:.2f}, Requested: ${amount:.2f}."
        )

    amount_cents = int(amount * 100)

    payout = s.Payout.create(
        amount=amount_cents,
        currency="usd",
        stripe_account=connect.stripe_account_id,
        metadata={"user_id": str(user.id)},
    )

    withdrawal = TipWithdrawal.objects.create(
        user=user,
        connect_account=connect,
        amount=amount,
        currency="usd",
        stripe_payout_id=payout.id,
        status=WithdrawalStatus.PENDING,
    )

    return withdrawal


def construct_stripe_event(payload, sig_header):
    """
    Verify the Stripe webhook signature and construct the event object.
    Raises stripe.error.SignatureVerificationError on failure.
    """
    s = _stripe()
    return s.Webhook.construct_event(
        payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
    )

