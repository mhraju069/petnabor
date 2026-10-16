"""
Admin configuration for the Story feature — PetNabor.
"""

from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from api.core.admin_mixins import UUIDSearchMixin
from unfold.decorators import display

from .models import Story, StoryReaction, StoryReply, StoryView


@admin.register(Story)
class StoryAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = [
        "short_id",
        "author",
        "display_media_type",
        "display_privacy",
        "views_count",
        "display_active",
        "expires_at",
        "created_at",
    ]
    list_select_related = ("author",)
    list_filter = ["media_type", "privacy"]
    search_fields = ["id", "author__email", "author__username", "text_content"]
    readonly_fields = ["id", "views_count", "created_at"]
    raw_id_fields = ["author"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]

    fieldsets = (
        (
            _("Content"),
            {
                "fields": (
                    "id",
                    "author",
                    "media_type",
                    "media",
                    "text_content",
                    "bg_color",
                ),
            },
        ),
        (
            _("Settings"),
            {
                "fields": ("privacy", "expires_at"),
            },
        ),
        (
            _("Stats"),
            {
                "classes": ("collapse",),
                "fields": ("views_count", "created_at"),
            },
        ),
    )

    @display(
        description=_("Type"),
        label={
            "TEXT": "info",
            "IMAGE": "success",
            "VIDEO": "warning",
        },
        ordering="media_type",
    )
    def display_media_type(self, obj):
        return obj.media_type

    @display(
        description=_("Privacy"),
        label={
            "PUBLIC": "success",
            "FRIENDS_ONLY": "info",
        },
        ordering="privacy",
    )
    def display_privacy(self, obj):
        return obj.privacy

    @display(
        description=_("Active"), label={True: "success", False: "danger"}, boolean=True
    )
    def display_active(self, obj):
        return obj.is_active


@admin.register(StoryView)
class StoryViewAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = ["short_id", "story", "viewer", "viewed_at"]
    list_select_related = ("story", "story__author", "viewer",)
    list_per_page = 50
    show_full_result_count = False
    raw_id_fields = ["story", "viewer"]
    readonly_fields = ["id", "viewed_at"]
    search_fields = ["id", "viewer__email", "viewer__username"]
    ordering = ["-viewed_at"]


@admin.register(StoryReaction)
class StoryReactionAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = ["short_id", "story", "user", "display_reaction_type", "created_at"]
    list_select_related = ("story", "story__author", "user",)
    list_filter = ["reaction_type"]
    raw_id_fields = ["story", "user"]
    readonly_fields = ["id", "created_at"]
    search_fields = ["id", "user__email", "user__username"]

    @display(
        description=_("Reaction"),
        label={
            "LIKE": "info",
            "LOVE": "danger",
            "HAHA": "warning",
            "SAD": "warning",
            "ANGRY": "danger",
            "WOW": "success",
        },
        ordering="reaction_type",
    )
    def display_reaction_type(self, obj):
        return obj.reaction_type


@admin.register(StoryReply)
class StoryReplyAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = ["short_id", "story", "user", "truncated_reply", "created_at"]
    list_select_related = ("story", "story__author", "user",)
    raw_id_fields = ["story", "user"]
    readonly_fields = ["id", "created_at"]
    search_fields = ["id", "user__email", "reply_text"]
    ordering = ["-created_at"]

    @display(description=_("Reply"))
    def truncated_reply(self, obj):
        text = obj.reply_text or "—"
        return text[:60] + "…" if len(text) > 60 else text
