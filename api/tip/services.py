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

    try:
        connect = user.stripe_connect_account
        try:
            onboarding_url = _get_account_link(connect.stripe_account_id)
            return connect, onboarding_url
        except stripe.error.InvalidRequestError as exc:
            if "platform" in str(exc).lower() or "no such destination" in str(exc).lower() or "does not exist" in str(exc).lower():
                logger.warning("Stripe Connect account %s invalid/unlinked during onboarding. Auto-deleting.", connect.stripe_account_id)
                connect.delete()
            else:
                raise
        except stripe.error.PermissionError as exc:
            logger.warning("Stripe Connect account %s permission denied during onboarding. Auto-deleting.", connect.stripe_account_id)
            connect.delete()
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
    except stripe.error.InvalidRequestError as exc:
        if "platform" in str(exc).lower() or "no such destination" in str(exc).lower() or "does not exist" in str(exc).lower():
            connect.delete()
            raise StripeConnectAccount.DoesNotExist
        raise
    except stripe.error.PermissionError as exc:
        connect.delete()
        raise StripeConnectAccount.DoesNotExist


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
    except stripe.error.InvalidRequestError as exc:
        if "platform" in str(exc).lower() or "no such destination" in str(exc).lower() or "does not exist" in str(exc).lower():
            logger.warning("Stripe Connect account %s invalid/unlinked during status fetch. Auto-deleting.", connect.stripe_account_id)
            connect.delete()
            return None
        raise
    except stripe.error.PermissionError as exc:
        logger.warning("Stripe Connect account %s permission denied during status fetch. Auto-deleting.", connect.stripe_account_id)
        connect.delete()
        return None

    connect.is_charges_enabled = account.charges_enabled
    connect.is_payouts_enabled = account.payouts_enabled
    connect.is_onboarding_complete = (
        account.details_submitted and account.charges_enabled
    )
    connect.save(
        update_fields=[
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
        except stripe.error.InvalidRequestError as exc:
            if "no such destination" in str(exc).lower() or "platform" in str(exc).lower() or "does not exist" in str(exc).lower():
                logger.warning("Recipient Stripe Connect account %s invalid. Auto-deleting and falling back to HELD.", connect.stripe_account_id)
                connect.delete()
                has_verified_account = False
            else:
                raise
        except stripe.error.PermissionError as exc:
            logger.warning("Recipient Stripe Connect account %s permission denied. Auto-deleting and falling back to HELD.", connect.stripe_account_id)
            connect.delete()
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

        if tip.status in (TipStatus.SUCCEEDED, TipStatus.FAILED, TipStatus.REFUNDED):
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
                    body=(
                        f"{tipper_name} sent you a ${tip.amount:.2f} tip. "
                        "Connect your bank account to receive it."
                    ),
                    user_id=tip.recipient_id,
                    notification_type=NotificationTypes.TIP_ENABLE_REQUEST,
                    data={"action": "connect_stripe", "tip_id": str(tip.id), "amount": str(tip.amount)},
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

        if tip.status in (TipStatus.SUCCEEDED, TipStatus.FAILED, TipStatus.REFUNDED):
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

        tip.status = TipStatus.CANCELLED
        tip.save(update_fields=["status", "updated_at"])


def handle_charge_refunded(charge):
    """Mark the tip as refunded when the charge is refunded."""
    charge_id = charge["id"]
    pi_id = charge.get("payment_intent")

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

    connect.is_charges_enabled = account.get("charges_enabled", False)
    connect.is_payouts_enabled = account.get("payouts_enabled", False)
    connect.is_onboarding_complete = (
        account.get("details_submitted", False) and connect.is_charges_enabled
    )
    connect.save(
        update_fields=[
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

