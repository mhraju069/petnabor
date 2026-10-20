"""
PetNabor Admin Dashboard utilities.

Provides:
  - dashboard_callback  → custom Unfold dashboard with KPI cards + charts
  - environment_callback → environment badge (Development / Production)
  - user_count_badge     → live user count shown in sidebar navigation
  - pending_reports_badge → unresolved reports count shown in sidebar
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


# ──────────────────────────────────────────────
# Sidebar badge callbacks
# ──────────────────────────────────────────────


def user_count_badge(request) -> str:
    """Show total active user count next to 'Users' in the sidebar."""
    from .models import User

    count = User.objects.filter(is_active=True).count()
    return str(count) if count else ""


def pending_reports_badge(request) -> str:
    """Show count of unresolved reports next to 'Reports' in the sidebar."""
    try:
        from api.report.models import Report

        count = Report.objects.filter(is_resolved=False).count()
        return str(count) if count else ""
    except Exception:
        return ""


# ──────────────────────────────────────────────
# Environment label
# ──────────────────────────────────────────────


def environment_callback(request):
    """Return a colored environment badge shown in the admin header."""
    from django.conf import settings

    if settings.DEBUG:
        return _("Development"), "warning"
    return _("Production"), "danger"


# ──────────────────────────────────────────────
# Dashboard callback
# ──────────────────────────────────────────────


def dashboard_callback(request, context: dict) -> dict:
    """
    Populate the Unfold custom dashboard with KPI cards and charts.

    All queries are lightweight aggregates — no N+1 issues.
    """
    from .models import User, UserTypes

    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    # ── KPI totals ────────────────────────────────────────────────────────────
    total_users = User.objects.filter(is_active=True).count()
    active_today = User.objects.filter(last_active__gte=today_start).count()
    new_this_week = User.objects.filter(created_at__gte=week_ago).count()

    try:
        from api.post.models import Post

        total_posts = Post.objects.filter(is_deleted=False).count()
    except Exception:
        total_posts = 0

    try:
        from api.pet.models import PetProfile

        total_pets = PetProfile.objects.count()
    except Exception:
        total_pets = 0

    try:
        from api.report.models import Report

        pending_reports = Report.objects.filter(is_resolved=False).count()
        # Open reports first: these are the ones needing a moderator.
        recent_reports = Report.objects.filter(is_resolved=False).order_by("-created_at")[:10]
        flagged_posts = Report.objects.filter(target_type="POST").order_by("-created_at")[:10]
    except Exception:
        pending_reports = 0
        recent_reports = []
        flagged_posts = []

    try:
        from api.product.models import Product, ProductEvent
        total_products = Product.objects.count()
        
        event_qs = (
            ProductEvent.objects.filter(created_at__gte=month_ago)
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(count=Count("id"))
            .order_by("day")
        )
        pe_labels = [str(row["day"]) for row in event_qs]
        pe_data = [row["count"] for row in event_qs]
    except Exception:
        total_products = 0
        pe_labels, pe_data = [], []

    try:
        from api.vendor.models import Vendor
        total_vendors = Vendor.objects.count()
        
        plan_qs = Vendor.objects.values("plan__name").annotate(count=Count("id")).order_by("-count")
        plan_labels = [row["plan__name"] or "No Plan" for row in plan_qs]
        plan_data = [row["count"] for row in plan_qs]
    except Exception:
        total_vendors = 0
        plan_labels, plan_data = [], []

    try:
        from api.wishlist.models import ProductWishlist
        total_wishlists = ProductWishlist.objects.count()
    except Exception:
        total_wishlists = 0


    # ── User registrations — last 30 days (for line chart) ────────────────────
    registration_qs = (
        User.objects.filter(created_at__gte=month_ago)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    # Every day of the last 30, including days with no sign-ups (otherwise
    # the line skips them and quiet days are invisible).
    by_day = {row["day"]: row["count"] for row in registration_qs}
    days = [(now - timedelta(days=offset)).date() for offset in range(29, -1, -1)]
    reg_labels = [day.strftime("%b %d") for day in days]
    reg_data = [by_day.get(day, 0) for day in days]

    # ── User type distribution (for donut chart) ───────────────────────────────
    user_type_qs = (
        User.objects.values("user_type")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    type_names = dict(UserTypes.choices)
    user_type_labels = [
        str(type_names.get(row["user_type"], row["user_type"].capitalize()))
        for row in user_type_qs
    ]
    user_type_data = [row["count"] for row in user_type_qs]

    # ── Top pet types ──────────────────────────────────────────────────────────
    try:
        from api.pet.models import PetProfile

        pet_type_qs = (
            PetProfile.objects.values("pet_type")
            .annotate(count=Count("id"))
            .order_by("-count")[:8]
        )
        pet_labels = [row["pet_type"] for row in pet_type_qs]
        pet_data = [row["count"] for row in pet_type_qs]
    except Exception:
        pet_labels, pet_data = [], []

    # ── Friend request statuses ────────────────────────────────────────────────
    try:
        from api.friends.models import FriendRequest

        fr_qs = (
            FriendRequest.objects.values("status")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        fr_labels = [row["status"].capitalize() for row in fr_qs]
        fr_data = [row["count"] for row in fr_qs]
    except Exception:
        fr_labels, fr_data = [], []

    # ── Recent new users (activity feed) ──────────────────────────────────────
    recent_users = User.objects.order_by("-created_at").select_related("profile")[:10]

    def admin_url(name: str, query: str = "") -> str:
        from django.urls import NoReverseMatch, reverse

        try:
            return reverse(f"admin:{name}") + query
        except NoReverseMatch:
            return ""

    # ── Build context ──────────────────────────────────────────────────────────
    context.update(
        {
            # KPI cards
            "kpi_cards": [
                {
                    "title": "Total Users",
                    "url": admin_url("users_user_changelist"),
                    "metric": f"{total_users:,}",
                    "icon": "people",
                    "color": "purple",
                },
                {
                    "title": "Active Today",
                    "url": admin_url("users_user_changelist"),
                    "metric": f"{active_today:,}",
                    "icon": "person_play",
                    "color": "green",
                },
                {
                    "title": "New this Week",
                    "url": admin_url("users_user_changelist"),
                    "metric": f"{new_this_week:,}",
                    "icon": "person_add",
                    "color": "blue",
                },
                {
                    "title": "Total Posts",
                    "url": admin_url("post_post_changelist"),
                    "metric": f"{total_posts:,}",
                    "icon": "article",
                    "color": "pink",
                },
                {
                    "title": "Total Pets",
                    "url": admin_url("pet_petprofile_changelist"),
                    "metric": f"{total_pets:,}",
                    "icon": "pets",
                    "color": "orange",
                },
                {
                    "title": "Pending Reports",
                    "url": admin_url("report_report_changelist", "?is_resolved__exact=0"),
                    "metric": f"{pending_reports:,}",
                    "icon": "flag",
                    "color": "red",
                    "positive": False,
                },
                {
                    "title": "Total Vendors",
                    "url": admin_url("vendor_vendor_changelist"),
                    "metric": f"{total_vendors:,}",
                    "icon": "storefront",
                    "color": "teal",
                },
                {
                    "title": "Total Products",
                    "url": admin_url("product_product_changelist"),
                    "metric": f"{total_products:,}",
                    "icon": "inventory_2",
                    "color": "indigo",
                },
                {
                    "title": "Total Wishlists",
                    "url": admin_url("wishlist_productwishlist_changelist"),
                    "metric": f"{total_wishlists:,}",
                    "icon": "favorite",
                    "color": "pink",
                },
            ],
            # Charts data (consumed by the dashboard template)
            "registration_chart": {
                "labels": reg_labels,
                "data": reg_data,
            },
            "user_type_chart": {
                "labels": user_type_labels,
                "data": user_type_data,
            },
            "pet_type_chart": {
                "labels": pet_labels,
                "data": pet_data,
            },
            "friend_request_chart": {
                "labels": fr_labels,
                "data": fr_data,
            },
            "vendor_plan_chart": {
                "labels": plan_labels,
                "data": plan_data,
            },
            "product_event_chart": {
                "labels": pe_labels,
                "data": pe_data,
            },
            # Same chart data as JSON for the template's charts (json_script —
            # safe with any label text, unlike pasting Python lists into JS).
            "dashboard_charts": {
                "registrations": {"labels": reg_labels, "data": reg_data},
                "user_types": {"labels": user_type_labels, "data": user_type_data},
                "pet_types": {"labels": pet_labels, "data": pet_data},
                "vendor_plans": {"labels": plan_labels, "data": plan_data},
                "product_events": {"labels": pe_labels, "data": pe_data},
            },
            # Activity feed
            "recent_users": recent_users,
            "recent_reports": recent_reports,
            "flagged_posts": flagged_posts,
        }
    )
    return context
