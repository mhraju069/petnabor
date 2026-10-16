"""
Admin configuration for the Referral system — PetNabor.
"""

from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from api.core.admin_mixins import UUIDSearchMixin
from unfold.decorators import display

from .models import ReferralSettings, ReferralTransaction, ReferralWallet


@admin.register(ReferralSettings)
class ReferralSettingsAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    """
    Singleton admin: prevents creating more than one settings row.
    Redirects directly to the single record.
    """

    list_display = ["short_id", "referrer_points", "referee_points", "updated_at"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        (
            _("Point Awards"),
            {
                "fields": ("referrer_points", "referee_points"),
                "description": "Points awarded when a user successfully refers a new member.",
            },
        ),
        (
            _("Timestamps"),
            {
                "classes": ("collapse",),
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    def has_add_permission(self, request):
        return not ReferralSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = ReferralSettings.objects.first()
        if obj:
            url = reverse(
                f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_change",
                args=[obj.pk],
            )
        else:
            url = reverse(
                f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_add"
            )
        return HttpResponseRedirect(url)


@admin.register(ReferralWallet)
class ReferralWalletAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = ["short_id", "user", "display_balance", "updated_at"]
    list_select_related = ("user",)
    search_fields = ["id", "user__email", "user__phone"]
    readonly_fields = ["id", "user", "balance", "created_at", "updated_at"]
    ordering = ["-balance"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description=_("Balance"), ordering="balance")
    def display_balance(self, obj):
        color = "green" if obj.balance > 0 else "gray"
        return f"{obj.balance:,.2f} pts"


@admin.register(ReferralTransaction)
class ReferralTransactionAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = [
        "short_id",
        "wallet",
        "transaction_type",
        "display_amount",
        "display_status",
        "related_user",
        "created_at",
    ]
    list_select_related = ("wallet", "wallet__user", "related_user",)
    list_filter = ["transaction_type", "status"]
    search_fields = ["id", "wallet__user__email", "related_user__email", "note"]
    ordering = ["-created_at"]
    readonly_fields = [f.name for f in ReferralTransaction._meta.fields]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description=_("Amount"), ordering="amount")
    def display_amount(self, obj):
        prefix = "+" if obj.amount >= 0 else ""
        return f"{prefix}{obj.amount:,.2f} pts"

    @display(
        description=_("Status"),
        label={
            "completed": "success",
            "pending": "warning",
            "failed": "danger",
        },
        ordering="status",
    )
    def display_status(self, obj):
        return obj.status
