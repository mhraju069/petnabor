"""
Tip System Models.

- TipSettings: singleton for admin-configurable commission %
- StripeConnectAccount: tracks each user's Stripe Express account
- Tip: one peer-to-peer tip payment
- TipWithdrawal: tracks payout/withdrawal requests
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models


# ──────────────────────────────────────────────
# Settings (Singleton)
# ──────────────────────────────────────────────


class TipSettings(models.Model):
    """
    Admin-editable global settings for the tipping system.

    There should only ever be ONE row in this table.
    Access via:  TipSettings.get_instance()
    """

    commission_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("20.00"),
        help_text="Platform commission percentage taken from each tip (e.g. 20.00 = 20%).",
    )
    minimum_tip_amount = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("3.00"),
        help_text=(
            "Minimum tip amount in USD. Stripe's card fee (2.9% + $0.30) is paid "
            "by the platform, so with a 20% commission tips under ~$1.76 lose money."
        ),
    )
    maximum_tip_amount = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("500.00"),
        help_text="Maximum tip amount in USD.",
    )
    minimum_withdrawal_amount = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("10.00"),
        help_text="Minimum withdrawal amount in USD.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Tip Settings"
        verbose_name_plural = "Tip Settings"

    def __str__(self):
        return f"Tip Settings — Commission: {self.commission_percentage}%"

    @classmethod
    def get_instance(cls):
        """Return the singleton settings row, creating it with defaults if absent."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ──────────────────────────────────────────────
# Stripe Connect Account
# ──────────────────────────────────────────────


class StripeConnectAccount(models.Model):
    """
    Tracks a user's Stripe Express Connected Account.
    A user must have a verified Connect account to receive tips.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="stripe_connect_account",
    )
    stripe_account_id = models.CharField(
        max_length=255,
        unique=True,
        help_text="Stripe Express Account ID (acct_xxx).",
    )
    is_onboarding_complete = models.BooleanField(
        default=False,
        help_text="True once the user has completed Stripe's KYC onboarding.",
    )
    is_charges_enabled = models.BooleanField(
        default=False,
        help_text="True when Stripe reports charges_enabled on the account.",
    )
    is_payouts_enabled = models.BooleanField(
        default=False,
        help_text="True when Stripe reports payouts_enabled on the account.",
    )
    needs_reconnect = models.BooleanField(
        default=False,
        help_text=(
            "Stripe says this account no longer exists or isn't accessible. Kept "
            "(never deleted) so the link to the user's money isn't lost; the user "
            "re-onboards to replace it."
        ),
    )
    # Switching to a different Stripe account: the new one is onboarded here
    # first; it replaces stripe_account_id once Stripe says it's ready.
    pending_stripe_account_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="New Stripe account being set up to replace the current one.",
    )
    switch_started_at = models.DateTimeField(null=True, blank=True)
    replaced_account_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="Stripe accounts this user used before (newest last), for support.",
    )
    last_available_balance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text=(
            "USD available on Stripe as we last saw it. A balance.available "
            "event above this means pending tips cleared: we notify the user."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Stripe Connect Account"
        verbose_name_plural = "Stripe Connect Accounts"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Connect({self.user}) — {self.stripe_account_id}"

    @property
    def is_fully_verified(self):
        return self.is_onboarding_complete and self.is_charges_enabled and self.is_payouts_enabled

    @property
    def is_switching(self):
        return bool(self.pending_stripe_account_id)

    @property
    def can_receive_tips(self):
        """Tips may go straight to this account (not while switching away from it)."""
        return self.is_fully_verified and not self.needs_reconnect and not self.is_switching


# ──────────────────────────────────────────────
# Tip
# ──────────────────────────────────────────────


class TipStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    HELD = "held", "Held — Awaiting Recipient Account"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    REFUNDED = "refunded", "Refunded"
    CANCELLED = "cancelled", "Cancelled"
    DISPUTED = "disputed", "Disputed (chargeback)"


class Tip(models.Model):
    """
    Represents one peer-to-peer tip payment.
    The 'tipper' sends a tip to the 'recipient' after a meeting.
    Platform takes commission_percentage from the tip amount.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    tipper = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="tips_sent",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="tips_received",
    )
    meeting = models.ForeignKey(
        "meeting.Meeting",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tips",
        help_text="The meeting this tip is associated with.",
    )

    # Amounts (in USD)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Total tip amount charged to tipper (USD).",
    )
    commission_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="Commission % applied at the time this tip was created (snapshot).",
    )
    commission_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Platform commission amount in USD.",
    )
    recipient_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Amount received by recipient after commission deduction.",
    )

    # Stripe identifiers
    stripe_payment_intent_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
    )
    stripe_charge_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
    )
    stripe_transfer_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text="Stripe Transfer ID — set when a held tip is released to the recipient.",
    )

    status = models.CharField(
        max_length=20,
        choices=TipStatus.choices,
        default=TipStatus.PENDING,
        db_index=True,
    )

    # Chargebacks (charge.dispute.* webhooks).
    stripe_dispute_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    status_before_dispute = models.CharField(max_length=20, blank=True, default="")
    funds_reversed = models.BooleanField(
        default=False,
        help_text="The recipient's share was pulled back from their account during a dispute.",
    )

    # Optional note from the tipper
    note = models.CharField(max_length=500, blank=True, default="")

    # Stripe currency (always lowercase, e.g. 'usd')
    currency = models.CharField(max_length=10, default="usd")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Tip"
        verbose_name_plural = "Tips"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tipper", "status"]),
            models.Index(fields=["recipient", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"Tip ${self.amount} from {self.tipper} to {self.recipient} [{self.status}]"


# ──────────────────────────────────────────────
# Withdrawal
# ──────────────────────────────────────────────


class WithdrawalStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PAID = "paid", "Paid"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class TipWithdrawal(models.Model):
    """
    Tracks a recipient's withdrawal request.
    Funds are paid out via Stripe Payout to their Connected Account.

    A payout record: kept when the account is deleted (user and Connect
    account links are cleared, not the row) for tax and Stripe
    reconciliation. `stripe_account_id` records which Stripe account paid
    out, even after the user switches accounts or is deleted.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tip_withdrawals",
    )
    connect_account = models.ForeignKey(
        StripeConnectAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="withdrawals",
    )
    stripe_account_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="Stripe Connect account the payout was made from (acct_xxx), as it was at the time.",
    )

    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Withdrawal amount in USD.",
    )
    currency = models.CharField(max_length=10, default="usd")

    stripe_payout_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
    )

    status = models.CharField(
        max_length=20,
        choices=WithdrawalStatus.choices,
        default=WithdrawalStatus.PENDING,
        db_index=True,
    )

    failure_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Tip Withdrawal"
        verbose_name_plural = "Tip Withdrawals"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self):
        who = self.user or "deleted user"
        return f"Withdrawal ${self.amount} by {who} [{self.status}]"
