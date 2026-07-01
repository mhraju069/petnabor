"""
Tip System Serializers.
"""

from decimal import Decimal
from django.conf import settings
from rest_framework import serializers

from .models import TipSettings, StripeConnectAccount, Tip, TipWithdrawal


# ──────────────────────────────────────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────────────────────────────────────


class TipSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = TipSettings
        fields = [
            "commission_percentage",
            "minimum_tip_amount",
            "maximum_tip_amount",
            "minimum_withdrawal_amount",
        ]
        read_only_fields = fields


# ──────────────────────────────────────────────────────────────────────────────
# Connect Account
# ──────────────────────────────────────────────────────────────────────────────


class StripeConnectAccountSerializer(serializers.ModelSerializer):
    is_fully_verified = serializers.BooleanField(read_only=True)

    class Meta:
        model = StripeConnectAccount
        fields = [
            "stripe_account_id",
            "is_onboarding_complete",
            "is_charges_enabled",
            "is_payouts_enabled",
            "is_fully_verified",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


# ──────────────────────────────────────────────────────────────────────────────
# Tip
# ──────────────────────────────────────────────────────────────────────────────


class TipUserSerializer(serializers.Serializer):
    """Minimal user info embedded in tip responses."""
    id = serializers.UUIDField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    email = serializers.EmailField()


class SendTipSerializer(serializers.Serializer):
    """Validates a tip send request from the mobile client."""
    recipient_id = serializers.UUIDField(
        help_text="UUID of the user to tip."
    )
    meeting_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="Optional UUID of the associated meeting.",
    )
    amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.50"),
        help_text="Tip amount in USD.",
    )
    note = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
        default="",
    )
    idempotency_key = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="Optional UUID for idempotency. Recommended to prevent duplicate tips.",
    )

    def validate_amount(self, value):
        tip_settings = TipSettings.get_instance()
        if value < tip_settings.minimum_tip_amount:
            raise serializers.ValidationError(
                f"Minimum tip amount is ${tip_settings.minimum_tip_amount}."
            )
        if value > tip_settings.maximum_tip_amount:
            raise serializers.ValidationError(
                f"Maximum tip amount is ${tip_settings.maximum_tip_amount}."
            )
        return value


class TipSerializer(serializers.ModelSerializer):
    tipper = TipUserSerializer(read_only=True)
    recipient = TipUserSerializer(read_only=True)
    meeting_id = serializers.UUIDField(source="meeting.id", read_only=True, allow_null=True)
    is_held = serializers.SerializerMethodField(
        help_text="True when payment is confirmed but recipient hasn't connected their account yet."
    )

    class Meta:
        model = Tip
        fields = [
            "id",
            "tipper",
            "recipient",
            "meeting_id",
            "amount",
            "commission_percentage",
            "commission_amount",
            "recipient_amount",
            "note",
            "status",
            "is_held",
            "currency",
            "stripe_payment_intent_id",
            "stripe_transfer_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_is_held(self, obj):
        return obj.status == "held"


# ──────────────────────────────────────────────────────────────────────────────
# Withdrawal
# ──────────────────────────────────────────────────────────────────────────────


class WithdrawSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        help_text="Withdrawal amount in USD.",
    )

    def validate_amount(self, value):
        tip_settings = TipSettings.get_instance()
        if value < tip_settings.minimum_withdrawal_amount:
            raise serializers.ValidationError(
                f"Minimum withdrawal amount is ${tip_settings.minimum_withdrawal_amount}."
            )
        return value


class TipWithdrawalSerializer(serializers.ModelSerializer):
    class Meta:
        model = TipWithdrawal
        fields = [
            "id",
            "amount",
            "currency",
            "status",
            "stripe_payout_id",
            "failure_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
