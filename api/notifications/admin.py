"""
Admin configuration for Notifications — PetNabor.
"""

from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from api.core.admin_mixins import UUIDSearchMixin
from unfold.decorators import display

from .models import FCMDevice, NotificationSettings, Notifications


@admin.register(NotificationSettings)
class NotificationSettingsAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = (
        "short_id",
        "user",
        "display_push",
        "display_email",
        "display_message",
    )
    list_select_related = ("user",)
    search_fields = ("id", "user__email", "user__username")
    ordering = ("user__id",)
    raw_id_fields = ("user",)
    readonly_fields = ("id", "created_at", "updated_at")

    fieldsets = (
        (
            _("User"),
            {"fields": ("id", "user")},
        ),
        (
            _("Core Channels"),
            {
                "fields": (
                    "push_notifications",
                    "email_notifications",
                    "message_notifications",
                    "system_notifications",
                ),
            },
        ),
        (
            _("Social"),
            {
                "classes": ("collapse",),
                "fields": (
                    "friend_request_notifications",
                    "like_notifications",
                    "comment_notifications",
                    "mention_notifications",
                ),
            },
        ),
        (
            _("Pet & Meetup"),
            {
                "classes": ("collapse",),
                "fields": (
                    "meetup_notifications",
                    "vendor_post_notifications",
                    "product_share_notifications",
                    "product_interest_notifications",
                ),
            },
        ),
        (
            _("Marketing"),
            {
                "classes": ("collapse",),
                "fields": ("marketing_notifications",),
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

    @display(
        description=_("Push"), label={True: "success", False: "danger"}, boolean=True
    )
    def display_push(self, obj):
        return obj.push_notifications

    @display(
        description=_("Email"), label={True: "success", False: "danger"}, boolean=True
    )
    def display_email(self, obj):
        return obj.email_notifications

    @display(
        description=_("Message"), label={True: "success", False: "danger"}, boolean=True
    )
    def display_message(self, obj):
        return obj.message_notifications


@admin.register(Notifications)
class NotificationsAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = (
        "short_id",
        "user",
        "title",
        "display_type",
        "display_read",
        "created_at",
    )
    list_select_related = ("user",)
    list_per_page = 50
    show_full_result_count = False
    list_filter = ("notification_type", "is_read")
    search_fields = ("id", "user__email", "user__username", "title", "body")
    ordering = ("-created_at",)
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("user",)
    date_hierarchy = "created_at"

    actions = ["mark_all_read"]

    @admin.action(description="✅ Mark selected notifications as read")
    def mark_all_read(self, request, queryset):
        count = queryset.update(is_read=True)
        self.message_user(request, f"{count} notification(s) marked as read.")

    @display(
        description=_("Type"),
        label={
            "system": "info",
            "security": "danger",
            "friend_request": "success",
            "like": "warning",
            "comment": "info",
            "message": "info",
            "referral_bonus": "success",
            "marketing": "warning",
        },
        ordering="notification_type",
    )
    def display_type(self, obj):
        return obj.notification_type

    @display(
        description=_("Read"), label={True: "success", False: "warning"}, boolean=True
    )
    def display_read(self, obj):
        return obj.is_read


@admin.register(FCMDevice)
class FCMDeviceAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    # custom_notifications: True once a new app build registered this
    # device; only those get pushes with the sender's photo.
    list_display = ("short_id", "user", "truncated_token", "custom_notifications", "created_at")
    list_filter = ("custom_notifications",)
    list_select_related = ("user",)
    search_fields = ("id", "user__email", "user__username", "registration_id")
    ordering = ("-created_at",)
    readonly_fields = ("id", "created_at")
    raw_id_fields = ("user",)

    @display(description=_("Token"))
    def truncated_token(self, obj):
        token = obj.registration_id or ""
        return token[:24] + "…" if len(token) > 24 else token
