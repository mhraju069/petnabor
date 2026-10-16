"""
Tip System Admin — Unfold-styled admin registration.
"""

from django.contrib import admin
from django.utils.html import format_html, mark_safe
from unfold.admin import ModelAdmin

from .models import TipSettings, StripeConnectAccount, Tip, TipWithdrawal


@admin.register(TipSettings)
class TipSettingsAdmin(ModelAdmin):
    list_display = (
        "commission_percentage",
        "minimum_tip_amount",
        "maximum_tip_amount",
        "minimum_withdrawal_amount",
        "updated_at",
    )
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        ("Commission", {
            "fields": ("commission_percentage",),
        }),
        ("Tip Limits", {
            "fields": ("minimum_tip_amount", "maximum_tip_amount"),
        }),
        ("Withdrawal Limits", {
            "fields": ("minimum_withdrawal_amount",),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    def has_add_permission(self, request):
        return not TipSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StripeConnectAccount)
class StripeConnectAccountAdmin(ModelAdmin):
    list_display = (
        "user",
        "stripe_account_id",
        "onboarding_status_badge",
        "is_charges_enabled",
        "is_payouts_enabled",
        "created_at",
    )
    list_select_related = ("user",)
    list_filter = ("is_onboarding_complete", "is_charges_enabled", "is_payouts_enabled")
    search_fields = ("user__email", "user__phone", "stripe_account_id")
    readonly_fields = (
        "stripe_account_id",
        "is_onboarding_complete",
        "is_charges_enabled",
        "is_payouts_enabled",
        "created_at",
        "updated_at",
    )

    @admin.display(description="Onboarding")
    def onboarding_status_badge(self, obj):
        if obj.is_fully_verified:
            return mark_safe('<span style="color:#16a34a;font-weight:600;">Verified</span>')
        elif obj.is_onboarding_complete:
            return mark_safe('<span style="color:#d97706;font-weight:600;">Partial</span>')
        return mark_safe('<span style="color:#dc2626;font-weight:600;">Pending</span>')


@admin.register(Tip)
class TipAdmin(ModelAdmin):
    list_display = (
        "id",
        "tipper",
        "recipient",
        "amount",
        "commission_percentage",
        "commission_amount",
        "recipient_amount",
        "status_badge",
        "created_at",
    )
    list_select_related = ("tipper", "recipient",)
    list_filter = ("status", "currency", "created_at")
    search_fields = (
        "tipper__email",
        "recipient__email",
        "stripe_payment_intent_id",
        "stripe_charge_id",
    )
    readonly_fields = (
        "tipper",
        "recipient",
        "meeting",
        "amount",
        "commission_percentage",
        "commission_amount",
        "recipient_amount",
        "stripe_payment_intent_id",
        "stripe_charge_id",
        "currency",
        "note",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"

    fieldsets = (
        ("Parties", {
            "fields": ("tipper", "recipient", "meeting"),
        }),
        ("Amounts", {
            "fields": (
                "amount",
                "commission_percentage",
                "commission_amount",
                "recipient_amount",
                "currency",
            ),
        }),
        ("Stripe", {
            "fields": ("stripe_payment_intent_id", "stripe_charge_id", "status"),
        }),
        ("Note", {
            "fields": ("note",),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {
            "pending": "#d97706",
            "succeeded": "#16a34a",
            "failed": "#dc2626",
            "refunded": "#7c3aed",
            "cancelled": "#6b7280",
        }
        color = colors.get(obj.status, "#6b7280")
        label = obj.get_status_display()
        return mark_safe(f'<span style="color:{color};font-weight:600;">{label}</span>')


@admin.register(TipWithdrawal)
class TipWithdrawalAdmin(ModelAdmin):
    list_display = (
        "id",
        "user",
        "amount",
        "currency",
        "status_badge",
        "stripe_payout_id",
        "created_at",
    )
    list_select_related = ("user",)
    list_filter = ("status", "currency", "created_at")
    search_fields = ("user__email", "user__phone", "stripe_payout_id")
    readonly_fields = (
        "user",
        "connect_account",
        "amount",
        "currency",
        "stripe_payout_id",
        "failure_message",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"

    fieldsets = (
        ("User", {
            "fields": ("user", "connect_account"),
        }),
        ("Payout", {
            "fields": ("amount", "currency", "status", "stripe_payout_id"),
        }),
        ("Failure", {
            "fields": ("failure_message",),
            "classes": ("collapse",),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {
            "pending": "#d97706",
            "paid": "#16a34a",
            "failed": "#dc2626",
            "cancelled": "#6b7280",
        }
        color = colors.get(obj.status, "#6b7280")
        label = obj.get_status_display()
        return mark_safe(f'<span style="color:{color};font-weight:600;">{label}</span>')
