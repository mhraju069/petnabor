"""
Admin configuration for Friends, Friendship, and UserBlock — PetNabor.
"""

from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from api.core.admin_mixins import UUIDSearchMixin
from unfold.decorators import display

from .models import FriendRequest, Friendship, UserBlock


@admin.register(FriendRequest)
class FriendRequestAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = ("short_id", "sender", "receiver", "display_status", "created_at")
    list_select_related = ("sender", "receiver",)
    list_filter = ("status",)
    search_fields = (
        "id",
        "sender__email",
        "sender__username",
        "receiver__email",
        "receiver__username",
    )
    ordering = ("-created_at",)
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("sender", "receiver")
    date_hierarchy = "created_at"

    actions = ["accept_requests", "reject_requests"]

    @admin.action(description="✅ Accept selected friend requests")
    def accept_requests(self, request, queryset):
        count = queryset.update(status="accepted")
        self.message_user(request, f"{count} request(s) accepted.")

    @admin.action(description="❌ Reject selected friend requests")
    def reject_requests(self, request, queryset):
        count = queryset.update(status="rejected")
        self.message_user(request, f"{count} request(s) rejected.")

    @display(
        description=_("Status"),
        label={
            "pending": "warning",
            "accepted": "success",
            "rejected": "danger",
        },
        ordering="status",
    )
    def display_status(self, obj):
        return obj.status


@admin.register(Friendship)
class FriendshipAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = ("short_id", "sender", "receiver", "created_at")
    list_select_related = ("sender", "receiver",)
    search_fields = (
        "id",
        "sender__email",
        "sender__username",
        "receiver__email",
        "receiver__username",
    )
    ordering = ("-created_at",)
    readonly_fields = ("id", "created_at")
    raw_id_fields = ("sender", "receiver")
    date_hierarchy = "created_at"


@admin.register(UserBlock)
class UserBlockAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = ("short_id", "blocker", "blocked_user", "created_at")
    list_select_related = ("blocker", "blocked_user",)
    search_fields = (
        "id",
        "blocker__email",
        "blocker__username",
        "blocked_user__email",
        "blocked_user__username",
    )
    ordering = ("-created_at",)
    readonly_fields = ("id", "created_at")
    raw_id_fields = ("blocker", "blocked_user")
    date_hierarchy = "created_at"
