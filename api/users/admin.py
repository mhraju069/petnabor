"""
Admin configuration for User, Profile, and OTPVerification — PetNabor.

Uses django-unfold's UnfoldModelAdmin for a premium admin UI.
"""

from django.contrib import admin
from django.utils.html import format_html, mark_safe
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from api.core.admin_mixins import UUIDSearchMixin
from unfold.decorators import display

from .models import OTPVerification, Profile, User

# Inline
# ──────────────────────────────────────────────


class ProfileInline(admin.StackedInline):
    model = Profile
    fk_name = "user"
    can_delete = False
    verbose_name_plural = "Profile"
    fields = (
        "bio",
        "city",
        "state",
        "date_of_birth",
        "referral_code",
        "referred_by",
    )
    raw_id_fields = ("referred_by",)
    extra = 0


# ──────────────────────────────────────────────
# User Admin (with inline Profile)
# ──────────────────────────────────────────────


@admin.register(User)
class UserAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = (
        "short_id",
        "email",
        "phone",
        "display_user_type",
        "display_verified",
        "display_online",
        "is_staff",
        "is_active",
        "created_at",
    )
    list_filter = (
        "is_staff",
        "is_active",
        "is_verified",
        "is_email_verified",
        "is_phone_verified",
        "user_type",
    )
    search_fields = ("id", "username", "email", "phone", "first_name", "last_name")
    ordering = ("-created_at",)
    readonly_fields = ("id", "created_at", "updated_at", "firebase_uid")
    inlines = [ProfileInline]

    fieldsets = (
        (
            _("Identity"),
            {
                "fields": (
                    "id",
                    "email",
                    "phone",
                    "username",
                    "first_name",
                    "last_name",
                ),
            },
        ),
        (
            _("Account Type"),
            {
                "fields": ("user_type", "is_petpal", "agree_to_terms_and_conditions", "ai_data_consent"),
            },
        ),
        (
            _("Verification"),
            {
                "fields": (
                    "is_verified",
                    "is_email_verified",
                    "is_phone_verified",
                    "firebase_uid",
                ),
            },
        ),
        (
            _("Permissions"),
            {
                "classes": ("collapse",),
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (
            _("Activity"),
            {
                "classes": ("collapse",),
                "fields": ("is_online", "last_active", "created_at", "updated_at"),
            },
        ),
    )

    actions = ["activate_users", "deactivate_users", "mark_verified"]

    # ── Bulk actions ──────────────────────────────────────────────────────────

    @admin.action(description="Activate selected users")
    def activate_users(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"{count} user(s) activated.")

    @admin.action(description="Deactivate selected users")
    def deactivate_users(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f"{count} user(s) deactivated.")

    @admin.action(description="Mark selected users as verified")
    def mark_verified(self, request, queryset):
        count = queryset.update(is_verified=True, is_email_verified=True)
        self.message_user(request, f"{count} user(s) marked as verified.")

    # ── Colored @display columns ──────────────────────────────────────────────

    @display(
        description=_("User Type"),
        label={
            "petnabor": "info",
            "petpal": "success",
            "vendor": "warning",
            "admin": "danger",
        },
        ordering="user_type",
    )
    def display_user_type(self, obj):
        return obj.user_type.capitalize() if obj.user_type else "—"

    @display(
        description=_("Verified"),
        label={True: "success", False: "danger"},
        boolean=True,
    )
    def display_verified(self, obj):
        return obj.is_verified

    @display(
        description=_("Online"), label={True: "success", False: "warning"}, boolean=True
    )
    def display_online(self, obj):
        return obj.is_online


@admin.register(Profile)
class ProfileAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = ("short_id", "user", "city", "state", "referral_code", "display_avatar")
    search_fields = (
        "id",
        "user__email",
        "user__username",
        "city",
        "state",
        "referral_code",
    )
    ordering = ("user__id",)
    raw_id_fields = ("user", "referred_by")

    @display(description=_("Avatar"))
    def display_avatar(self, obj):
        if obj.profile_picture:
            return format_html(
                '<img src="{}" width="40" height="40" style="border-radius:50%;object-fit:cover;" />',
                obj.profile_picture.url,
            )
        return mark_safe(
            '<span class="material-symbols-outlined" style="font-size:32px;color:#9ca3af;">account_circle</span>'
        )


@admin.register(OTPVerification)
class OTPVerificationAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = (
        "short_id",
        "user",
        "otp_type",
        "display_used",
        "attempt_count",
        "expires_at",
        "created_at",
    )
    list_filter = ("otp_type", "is_used")
    search_fields = ("id", "user__email", "user__phone")
    ordering = ("-created_at",)
    readonly_fields = ("id", "otp_hash", "created_at")
    raw_id_fields = ("user",)

    @display(
        description=_("Used"), label={True: "success", False: "warning"}, boolean=True
    )
    def display_used(self, obj):
        return obj.is_used
