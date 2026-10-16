"""
Admin configuration for Messaging — ChatThread, ThreadParticipant, Message — PetNabor.
"""

from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin as UnfoldModelAdmin, TabularInline
from api.core.admin_mixins import UUIDSearchMixin
from unfold.decorators import display

from .models import ChatThread, Message, ThreadParticipant

# ──────────────────────────────────────────────
# Inline: participants inside ChatThreadAdmin
# ──────────────────────────────────────────────


class ThreadParticipantInline(TabularInline):
    model = ThreadParticipant
    extra = 0
    fields = ["user", "display_role", "is_muted", "joined_at", "left_at"]
    readonly_fields = ["joined_at", "display_role"]
    can_delete = False

    @display(
        description=_("Role"),
        label={
            "ADMIN": "danger",
            "MEMBER": "info",
        },
    )
    def display_role(self, obj):
        return obj.role


# ──────────────────────────────────────────────
# ChatThread Admin
# ──────────────────────────────────────────────


@admin.register(ChatThread)
class ChatThreadAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = [
        "short_id",
        "display_thread_name",
        "display_thread_type",
        "created_by",
        "last_message_timestamp",
        "created_at",
    ]
    list_select_related = ("created_by",)
    list_filter = ["thread_type"]
    search_fields = ["id", "name", "created_by__email", "created_by__username"]
    ordering = ["-last_message_timestamp"]
    readonly_fields = ["id", "created_at", "updated_at"]
    raw_id_fields = ["created_by"]
    inlines = [ThreadParticipantInline]
    date_hierarchy = "created_at"

    fieldsets = (
        (
            _("Thread Info"),
            {
                "fields": ("id", "thread_type", "created_by"),
            },
        ),
        (
            _("Group Details"),
            {
                "description": "Only applies to GROUP threads.",
                "fields": ("name", "description", "avatar_url"),
            },
        ),
        (
            _("Last Message"),
            {
                "classes": ("collapse",),
                # The text itself is private (see MessageAdmin).
                "fields": ("last_message_timestamp",),
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

    @display(description=_("Thread"), ordering="name")
    def display_thread_name(self, obj):
        return obj.name or f"DM {str(obj.id)[:8]}…"

    @display(
        description=_("Type"),
        label={
            "DIRECT": "info",
            "GROUP": "success",
        },
        ordering="thread_type",
    )
    def display_thread_type(self, obj):
        return obj.thread_type


# ──────────────────────────────────────────────
# ThreadParticipant Admin
# ──────────────────────────────────────────────


@admin.register(ThreadParticipant)
class ThreadParticipantAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = [
        "short_id",
        "thread",
        "user",
        "display_role",
        "display_muted",
        "joined_at",
        "left_at",
    ]
    list_select_related = ("thread", "user",)
    list_filter = ["role", "is_muted"]
    search_fields = ["id", "user__email", "user__username"]
    readonly_fields = ["id", "joined_at"]
    raw_id_fields = ["thread", "user"]
    ordering = ["-joined_at"]

    @display(
        description=_("Role"),
        label={
            "ADMIN": "danger",
            "MEMBER": "info",
        },
        ordering="role",
    )
    def display_role(self, obj):
        return obj.role

    @display(
        description=_("Muted"), label={True: "warning", False: "success"}, boolean=True
    )
    def display_muted(self, obj):
        return obj.is_muted


# ──────────────────────────────────────────────
# Message Admin
# ──────────────────────────────────────────────


@admin.register(Message)
class MessageAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    """
    Chat messages are private. Staff see metadata (who, when, type, status)
    but not the content — except for messages someone has reported, which
    moderators need to review. Messages can't be edited here, and content
    isn't searchable.
    """

    list_display = [
        "short_id",
        "sender",
        "display_preview",
        "display_msg_type",
        "thread",
        "display_deleted",
        "display_read",
        "created_at",
    ]
    list_per_page = 50
    show_full_result_count = False
    list_filter = ["message_type", "is_deleted_for_everyone", "is_read", "is_edited"]
    search_fields = ["id", "sender__email", "sender__username"]
    list_select_related = ["sender", "thread"]
    readonly_fields = ["id", "created_at", "updated_at"]
    raw_id_fields = ["thread", "sender", "reply_to"]
    fields = [
        "id",
        "thread",
        "sender",
        "message_type",
        "reported_content",
        "is_read",
        "is_edited",
        "is_deleted_for_everyone",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]
    date_hierarchy = "created_at"

    actions = ["mark_deleted_for_everyone"]

    def get_queryset(self, request):
        from django.db.models import Exists, OuterRef

        from api.report.models import Report

        reported = Report.objects.filter(target_type="MESSAGE", target_id=OuterRef("pk"))
        return super().get_queryset(request).annotate(is_reported=Exists(reported))

    def get_readonly_fields(self, request, obj=None):
        return self.fields  # view-only: nothing is editable

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.action(description="Delete selected messages for everyone")
    def mark_deleted_for_everyone(self, request, queryset):
        # Per message (not queryset.update) so attached photos are removed
        # from storage too.
        count = 0
        for message in queryset.filter(is_deleted_for_everyone=False):
            message.is_deleted_for_everyone = True
            message.text_content = None
            message.media_url = None
            message.media_file = None  # django-cleanup deletes the file
            message.save(
                update_fields=[
                    "is_deleted_for_everyone", "text_content", "media_url", "media_file", "updated_at",
                ]
            )
            count += 1
        self.message_user(request, f"{count} message(s) deleted for everyone.")

    def _reported(self, obj) -> bool:
        flag = getattr(obj, "is_reported", None)
        if flag is None:
            from api.report.models import Report

            flag = Report.objects.filter(target_type="MESSAGE", target_id=obj.pk).exists()
        return bool(flag)

    @display(description=_("Preview"))
    def display_preview(self, obj):
        if obj.is_deleted_for_everyone:
            return "⛔ Deleted"
        if not self._reported(obj):
            return f"🔒 Private {obj.message_type.lower()}"
        text = obj.text_content or obj.message_type
        return "🚩 " + (text[:50] + "…" if len(text) > 50 else text)

    @display(description=_("Content"))
    def reported_content(self, obj):
        from django.utils.html import format_html

        if obj.is_deleted_for_everyone:
            return "Deleted for everyone."
        if not self._reported(obj):
            return "Private — content is only shown for reported messages."
        if obj.media_url:
            return format_html(
                '{}<br><a href="{}" target="_blank" rel="noopener">Open attachment</a>',
                obj.text_content or "",
                obj.media_url,
            )
        return obj.text_content or "—"

    @display(
        description=_("Type"),
        label={
            "TEXT": "info",
            "IMAGE": "success",
            "VIDEO": "warning",
            "AUDIO": "warning",
            "FILE": "info",
            "SYSTEM": "danger",
        },
        ordering="message_type",
    )
    def display_msg_type(self, obj):
        return obj.message_type

    @display(
        description=_("Deleted"), label={True: "danger", False: "success"}, boolean=True
    )
    def display_deleted(self, obj):
        return obj.is_deleted_for_everyone

    @display(
        description=_("Read"), label={True: "success", False: "warning"}, boolean=True
    )
    def display_read(self, obj):
        return obj.is_read
