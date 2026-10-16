"""
Admin configuration for Meeting and MeetingFeedback — PetNabor.
"""

from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin as UnfoldModelAdmin, TabularInline
from api.core.admin_mixins import UUIDSearchMixin
from unfold.decorators import display

from .models import Meeting, MeetingFeedback

# ──────────────────────────────────────────────
# Inline: feedback inside MeetingAdmin
# ──────────────────────────────────────────────


class MeetingFeedbackInline(TabularInline):
    model = MeetingFeedback
    extra = 0
    readonly_fields = (
        "id",
        "reviewer",
        "reviewee",
        "rating",
        "is_public",
        "created_at",
    )
    fields = ("reviewer", "reviewee", "rating", "is_public", "feedback_text")
    can_delete = False


# ──────────────────────────────────────────────
# Meeting Admin
# ──────────────────────────────────────────────


@admin.register(Meeting)
class MeetingAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = (
        "short_id",
        "sender",
        "receiver",
        "visitor_name",
        "reason",
        "visit_date",
        "visit_time",
        "display_status",
        "created_at",
    )
    list_select_related = ("sender", "receiver",)
    list_filter = ("status", "reason", "visit_date")
    search_fields = (
        "id",
        "sender__email",
        "sender__username",
        "receiver__email",
        "receiver__username",
        "visitor_name",
        "city",
    )
    ordering = ("-created_at",)
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("sender", "receiver")
    date_hierarchy = "visit_date"
    inlines = [MeetingFeedbackInline]

    fieldsets = (
        (
            _("Participants"),
            {
                "fields": ("id", "sender", "receiver"),
            },
        ),
        (
            _("Visit Details"),
            {
                "fields": (
                    "visitor_name",
                    "visitor_phone",
                    "visit_date",
                    "visit_time",
                    "reason",
                    "message",
                ),
            },
        ),
        (
            _("Location"),
            {
                "fields": ("address_street", "city", "state", "zipcode"),
            },
        ),
        (
            _("Status"),
            {
                "fields": ("status",),
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

    actions = ["accept_meetings", "cancel_meetings", "complete_meetings"]

    @admin.action(description="✅ Accept selected meetings")
    def accept_meetings(self, request, queryset):
        count = queryset.filter(status="PENDING").update(status="ACCEPTED")
        self.message_user(request, f"{count} meeting(s) accepted.")

    @admin.action(description="❌ Cancel selected meetings")
    def cancel_meetings(self, request, queryset):
        count = queryset.exclude(status="COMPLETED").update(status="CANCELLED")
        self.message_user(request, f"{count} meeting(s) cancelled.")

    @admin.action(description="Mark selected meetings as completed")
    def complete_meetings(self, request, queryset):
        count = queryset.filter(status="ACCEPTED").update(status="COMPLETED")
        self.message_user(request, f"{count} meeting(s) marked as completed.")

    @display(
        description=_("Status"),
        label={
            "PENDING": "warning",
            "ACCEPTED": "info",
            "CANCELLED": "danger",
            "COMPLETED": "success",
        },
        ordering="status",
    )
    def display_status(self, obj):
        return obj.status


# ──────────────────────────────────────────────
# Meeting Feedback Admin
# ──────────────────────────────────────────────


@admin.register(MeetingFeedback)
class MeetingFeedbackAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = (
        "short_id",
        "reviewer",
        "reviewee",
        "meeting",
        "display_rating",
        "display_public",
        "created_at",
    )
    list_select_related = ("reviewer", "reviewee", "meeting",)
    list_filter = ("is_public",)
    search_fields = (
        "id",
        "reviewer__email",
        "reviewer__username",
        "reviewee__email",
        "reviewee__username",
    )
    ordering = ("-created_at",)
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("meeting", "reviewer", "reviewee")

    @display(description=_("Rating"), ordering="rating")
    def display_rating(self, obj):
        if obj.rating is None:
            return "—"
        stars = "★" * int(obj.rating) + "☆" * (5 - int(obj.rating))
        return f"{stars} ({obj.rating})"

    @display(
        description=_("Public"), label={True: "success", False: "warning"}, boolean=True
    )
    def display_public(self, obj):
        return obj.is_public
