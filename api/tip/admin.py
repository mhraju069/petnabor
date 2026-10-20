"""
Tip System Admin — Unfold-styled admin registration.
"""

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.html import format_html, mark_safe
from unfold.admin import ModelAdmin
from unfold.decorators import action

from . import services
from .models import TipSettings, StripeConnectAccount, Tip, TipStatus, TipWithdrawal


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
        "needs_reconnect",
        "created_at",
    )
    list_select_related = ("user",)
    list_filter = ("needs_reconnect", "is_onboarding_complete", "is_charges_enabled", "is_payouts_enabled")
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
        "stripe_transfer_id",
        "stripe_dispute_id",
        "status_before_dispute",
        "funds_reversed",
        "status",
        "currency",
        "note",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"
    # Refunds go through here, not the Stripe Dashboard: this also takes the
    # money back from the recipient and refunds the platform fee.
    actions_detail = ["refund_detail"]

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
            "fields": ("stripe_payment_intent_id", "stripe_charge_id", "stripe_transfer_id", "status"),
        }),
        ("Dispute (chargeback)", {
            "fields": ("stripe_dispute_id", "status_before_dispute", "funds_reversed"),
            "classes": ("collapse",),
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
            "held": "#2563eb",
            "disputed": "#be123c",
        }
        color = colors.get(obj.status, "#6b7280")
        label = obj.get_status_display()
        return mark_safe(f'<span style="color:{color};font-weight:600;">{label}</span>')

    @action(description="Refund tip", url_path="refund")
    def refund_detail(self, request, object_id):
        """GET: confirmation page. POST (CSRF): refund via services.refund_tip."""
        if not self.has_change_permission(request):
            raise PermissionDenied
        tip = get_object_or_404(Tip.objects.select_related("tipper", "recipient"), pk=object_id)
        change_url = reverse("admin:tip_tip_change", args=[object_id])

        if request.method == "POST":
            try:
                services.refund_tip(tip)
                self.message_user(
                    request, f"Tip refunded: ${tip.amount} back to the tipper.", messages.SUCCESS
                )
            except services.RefundError as exc:
                self.message_user(request, f"Couldn't refund: {exc}", messages.ERROR)
            return redirect(change_url)

        if tip.status == TipStatus.HELD and not tip.stripe_charge_id:
            effect = "The tipper never finished paying, so the payment is just cancelled."
        elif tip.status == TipStatus.HELD:
            effect = (
                f"The full ${tip.amount} goes back to the tipper. The recipient never "
                "received it (they haven't set up payouts)."
            )
        elif tip.status == TipStatus.SUCCEEDED:
            effect = (
                f"The full ${tip.amount} goes back to the tipper, and the recipient's "
                f"${tip.recipient_amount} is taken back from their Stripe account "
                "(fails if they've already withdrawn it). The platform fee is refunded too."
            )
        else:
            effect = f"A {tip.get_status_display().lower()} tip can't be refunded."

        context = {
            **self.admin_site.each_context(request),
            "title": "Refund this tip?",
            "tip": tip,
            "effect": effect,
            "can_refund": tip.status in (TipStatus.HELD, TipStatus.SUCCEEDED),
            "cancel_url": change_url,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/tip/confirm_refund.html", context)


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
