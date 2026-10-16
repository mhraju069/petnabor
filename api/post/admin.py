"""
Admin configuration for the Post feature — PetNabor.
"""

from django.contrib import admin
from django.utils.html import format_html, mark_safe
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin as UnfoldModelAdmin, TabularInline
from api.core.admin_mixins import UUIDSearchMixin
from unfold.decorators import display

from .models import Hashtag, Post, PostComment, PostLike, PostMedia, SavedPost

# ──────────────────────────────────────────────
# Inline: PostMedia inside PostAdmin
# ──────────────────────────────────────────────


class PostMediaInline(TabularInline):
    model = PostMedia
    extra = 0
    readonly_fields = (
        "id",
        "media_type",
        "processing_status",
        "order",
        "display_thumb",
        "created_at",
    )
    fields = ("display_thumb", "media_type", "processing_status", "order")
    can_delete = False
    show_change_link = False

    @display(description=_("Preview"))
    def display_thumb(self, obj):
        file = obj.thumbnail_file or obj.file
        if file and obj.media_type == "IMAGE":
            return format_html(
                '<img src="{}" width="60" height="60" style="object-fit:cover;border-radius:6px;" />',
                file.url,
            )
        if obj.media_type == "VIDEO":
            return mark_safe("<span class='material-symbols-outlined' style='font-size:24px;color:#9ca3af;'>movie</span>")
        return "—"


# ──────────────────────────────────────────────
# Post Admin
# ──────────────────────────────────────────────


@admin.register(Post)
class PostAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = (
        "short_id",
        "truncated_content",
        "author",
        "display_privacy",
        "likes_count",
        "comments_count",
        "display_deleted",
        "created_at",
    )
    list_select_related = ("author",)
    list_per_page = 50
    show_full_result_count = False
    list_filter = ("privacy", "is_deleted", "is_edited")
    search_fields = ("id", "content_text", "author__email", "author__username")
    ordering = ("-created_at",)
    readonly_fields = (
        "id",
        "likes_count",
        "comments_count",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("author",)
    date_hierarchy = "created_at"
    inlines = [PostMediaInline]

    actions = ["soft_delete_posts", "restore_posts"]

    @admin.action(description="Soft-delete selected posts")
    def soft_delete_posts(self, request, queryset):
        count = queryset.update(is_deleted=True)
        self.message_user(request, f"{count} post(s) soft-deleted.")

    @admin.action(description="♻️ Restore selected posts")
    def restore_posts(self, request, queryset):
        count = queryset.update(is_deleted=False)
        self.message_user(request, f"{count} post(s) restored.")

    @display(description=_("Content"))
    def truncated_content(self, obj):
        text = obj.content_text or "—"
        return text[:60] + "…" if len(text) > 60 else text

    @display(
        description=_("Privacy"),
        label={
            "PUBLIC": "success",
            "FRIENDS_ONLY": "info",
            "PRIVATE": "warning",
        },
        ordering="privacy",
    )
    def display_privacy(self, obj):
        return obj.privacy

    @display(
        description=_("Deleted"), label={True: "danger", False: "success"}, boolean=True
    )
    def display_deleted(self, obj):
        return obj.is_deleted


# ──────────────────────────────────────────────
# Post Media Admin
# ──────────────────────────────────────────────


@admin.register(PostMedia)
class PostMediaAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = ("short_id", "post", "media_type", "display_status", "order", "created_at")
    list_select_related = ("post", "post__author",)
    search_fields = ("id", "post__id")
    list_filter = ("media_type", "processing_status")
    raw_id_fields = ("post",)
    readonly_fields = ("id", "created_at")

    @display(
        description=_("Status"),
        label={
            "PENDING": "warning",
            "DONE": "success",
            "FAILED": "danger",
        },
        ordering="processing_status",
    )
    def display_status(self, obj):
        return obj.processing_status


# ──────────────────────────────────────────────
# Post Like Admin
# ──────────────────────────────────────────────


@admin.register(PostLike)
class PostLikeAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = ("short_id", "post", "user", "reaction_type", "created_at")
    list_select_related = ("post", "post__author", "user",)
    list_per_page = 50
    show_full_result_count = False
    list_filter = ("reaction_type",)
    raw_id_fields = ("post", "user")
    readonly_fields = ("id", "created_at")
    search_fields = ("id", "user__email", "post__id")


# ──────────────────────────────────────────────
# Post Comment Admin
# ──────────────────────────────────────────────


@admin.register(PostComment)
class PostCommentAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = (
        "short_id",
        "user",
        "truncated_comment",
        "post",
        "display_edited",
        "replies_count",
        "created_at",
    )
    list_select_related = ("user", "post", "post__author",)
    list_per_page = 50
    show_full_result_count = False
    list_filter = ("is_edited",)
    raw_id_fields = ("post", "user", "parent_comment")
    readonly_fields = ("id", "replies_count", "created_at", "updated_at")
    search_fields = ("id", "comment_text", "user__email")

    @display(description=_("Comment"))
    def truncated_comment(self, obj):
        text = obj.comment_text or "—"
        return text[:50] + "…" if len(text) > 50 else text

    @display(
        description=_("Edited"), label={True: "warning", False: "success"}, boolean=True
    )
    def display_edited(self, obj):
        return obj.is_edited


# ──────────────────────────────────────────────
# Saved Post Admin
# ──────────────────────────────────────────────


@admin.register(SavedPost)
class SavedPostAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = ("short_id", "user", "post", "created_at")
    list_select_related = ("user", "post", "post__author",)
    raw_id_fields = ("user", "post")
    readonly_fields = ("id", "created_at")
    search_fields = ("id", "user__email", "post__id")


# ──────────────────────────────────────────────
# Hashtag Admin
# ──────────────────────────────────────────────


@admin.register(Hashtag)
class HashtagAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = ("short_id", "name", "created_at")
    search_fields = (
        "id",
        "name",
    )
    ordering = ("name",)
