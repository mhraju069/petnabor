"""
Admin configuration for Blog — PetNabor.
"""

from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from api.core.admin_mixins import UUIDSearchMixin
from unfold.decorators import display

from .models import Blog, BlogCategory, BlogComment, BlogLike, BlogViewTracker


@admin.register(BlogCategory)
class BlogCategoryAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = ("short_id", "name", "slug", "created_at")
    search_fields = (
        "id",
        "name",
    )
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("name",)


@admin.register(Blog)
class BlogAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = (
        "short_id",
        "title",
        "author",
        "category",
        "display_published",
        "views_count",
        "likes_count",
        "display_deleted",
        "created_at",
    )
    list_select_related = ("author", "category",)
    list_filter = ("is_published", "is_deleted", "category")
    search_fields = ("id", "title", "author__username", "author__email")
    raw_id_fields = ("author",)
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    fieldsets = (
        (
            _("Content"),
            {
                "fields": (
                    "author",
                    "category",
                    "title",
                    "slug",
                    "content_body",
                    "cover_image",
                ),
            },
        ),
        (
            _("SEO & Metadata"),
            {
                "fields": ("meta_title", "meta_description", "tags"),
            },
        ),
        (
            _("Status"),
            {
                "fields": ("is_published", "published_at", "is_deleted"),
            },
        ),
        (
            _("Counters"),
            {
                "classes": ("collapse",),
                "fields": (
                    "views_count",
                    "likes_count",
                    "comments_count",
                    "shares_count",
                ),
            },
        ),
    )
    readonly_fields = (
        "id",
        "views_count",
        "likes_count",
        "comments_count",
        "shares_count",
    )
    prepopulated_fields = {"slug": ("title",)}

    actions = ["publish_blogs", "unpublish_blogs", "soft_delete_blogs"]

    @admin.action(description="Publish selected blogs")
    def publish_blogs(self, request, queryset):
        from django.utils import timezone

        count = queryset.update(is_published=True, published_at=timezone.now())
        self.message_user(request, f"{count} blog(s) published.")

    @admin.action(description="Unpublish selected blogs")
    def unpublish_blogs(self, request, queryset):
        count = queryset.update(is_published=False)
        self.message_user(request, f"{count} blog(s) unpublished.")

    @admin.action(description="Soft-delete selected blogs")
    def soft_delete_blogs(self, request, queryset):
        count = queryset.update(is_deleted=True, is_published=False)
        self.message_user(request, f"{count} blog(s) soft-deleted.")

    @display(
        description=_("Published"),
        label={True: "success", False: "warning"},
        boolean=True,
    )
    def display_published(self, obj):
        return obj.is_published

    @display(
        description=_("Deleted"), label={True: "danger", False: "success"}, boolean=True
    )
    def display_deleted(self, obj):
        return obj.is_deleted


@admin.register(BlogLike)
class BlogLikeAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = ("short_id", "blog", "user", "created_at")
    list_select_related = ("blog", "user",)
    raw_id_fields = ("blog", "user")
    search_fields = ("id", "user__email", "blog__title")
    readonly_fields = ("id", "created_at")


@admin.register(BlogComment)
class BlogCommentAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = (
        "short_id",
        "user",
        "blog",
        "display_edited",
        "display_deleted",
        "created_at",
    )
    list_select_related = ("user", "blog",)
    list_filter = ("is_edited", "is_deleted")
    raw_id_fields = ("blog", "user", "parent_comment")
    search_fields = ("id", "comment_text", "user__username", "blog__title")
    readonly_fields = ("id", "replies_count", "created_at", "updated_at")

    @display(
        description=_("Edited"), label={True: "warning", False: "success"}, boolean=True
    )
    def display_edited(self, obj):
        return obj.is_edited

    @display(
        description=_("Deleted"), label={True: "danger", False: "success"}, boolean=True
    )
    def display_deleted(self, obj):
        return obj.is_deleted


@admin.register(BlogViewTracker)
class BlogViewTrackerAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = ("short_id", "blog", "user", "ip_address", "created_at")
    list_select_related = ("blog", "user",)
    list_per_page = 50
    show_full_result_count = False
    raw_id_fields = ("blog", "user")
    search_fields = ("id", "ip_address", "blog__title")
    readonly_fields = ("id", "created_at")
