"""
Admin configuration for the Report feature — PetNabor.

Moderators see what was reported (text, media, author) and act on it from
the report itself: remove the content, suspend the author, or dismiss.
Destructive actions ask for confirmation. See api/report/moderation.py.
"""

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.decorators import action, display

from api.core.admin_mixins import UUIDSearchMixin

from . import moderation
from .models import Report


@admin.register(Report)
class ReportAdmin(UUIDSearchMixin, UnfoldModelAdmin):
    list_display = (
        "short_id",
        "reporter",
        "display_target",
        "display_owner",
        "reason",
        "display_resolved",
        "created_at",
    )
    list_filter = ("target_type", "is_resolved")
    search_fields = (
        "id",
        "target_id",
        "reporter__email",
        "reporter__phone",
        "reason",
        "description",
    )
    ordering = ("is_resolved", "-created_at")  # open reports first
    readonly_fields = (
        "id",
        "reporter",
        "target_type",
        "target_id",
        "reported_content",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"

    fieldsets = (
        (_("Reported content"), {"fields": ("reported_content",)}),
        (_("Report details"), {"fields": ("id", "reporter", "target_type", "target_id")}),
        (_("Reason"), {"fields": ("reason", "description")}),
        (_("Resolution"), {"fields": ("is_resolved",)}),
        (
            _("Timestamps"),
            {"classes": ("collapse",), "fields": ("created_at", "updated_at")},
        ),
    )

    actions = ["remove_content_bulk", "suspend_owner_bulk", "mark_resolved", "mark_unresolved"]
    # Buttons on the report's own page.
    actions_detail = ["remove_content_detail", "suspend_owner_detail", "dismiss_detail"]

    # ── Columns / preview ────────────────────────────────────────────

    @display(description=_("Target"), ordering="target_type")
    def display_target(self, obj):
        summary = moderation.describe_target(obj)
        label = f"{obj.get_target_type_display()} · {summary.status}"
        if summary.url:
            return format_html('<a href="{}">{}</a>', summary.url, label)
        return label

    @display(description=_("Posted by"))
    def display_owner(self, obj):
        owner = moderation.target_owner(obj)
        if owner is None:
            return "—"
        name = owner.email or owner.username or str(owner.pk)
        url = moderation.admin_url(owner)
        suffix = "" if owner.is_active else " (suspended)"
        return format_html('<a href="{}">{}</a>{}', url, name, suffix) if url else f"{name}{suffix}"

    @display(description=_("Reported content"))
    def reported_content(self, obj):
        summary = moderation.describe_target(obj)
        if not summary.exists:
            return format_html("<em>{}</em>", "This item no longer exists.")

        owner = summary.owner
        owner_html = "—"
        if owner is not None:
            owner_url = moderation.admin_url(owner)
            owner_name = owner.email or owner.username or str(owner.pk)
            owner_html = (
                format_html('<a href="{}">{}</a>', owner_url, owner_name) if owner_url else owner_name
            )
            if not owner.is_active:
                owner_html = format_html("{} <strong>(suspended)</strong>", owner_html)

        media_html = format_html_join(
            "",
            '<a href="{0}" target="_blank" rel="noopener"><img src="{0}" '
            'style="height:96px;width:96px;object-fit:cover;border-radius:10px;margin:4px 6px 0 0"></a>',
            ((url,) for url in summary.media[:6]),
        )
        open_html = (
            format_html('<a href="{}">Open in admin →</a>', summary.url) if summary.url else ""
        )
        return format_html(
            '<div style="line-height:1.6">'
            "<div><strong>Status:</strong> {}</div>"
            "<div><strong>Posted by:</strong> {}</div>"
            '<div style="margin-top:8px;white-space:pre-wrap">{}</div>'
            "<div>{}</div>"
            '<div style="margin-top:8px">{}</div>'
            "</div>",
            summary.status,
            owner_html,
            summary.text or "(no text)",
            media_html,
            open_html,
        )

    @display(
        description=_("Resolved"),
        label={True: "success", False: "danger"},
        boolean=True,
    )
    def display_resolved(self, obj):
        return obj.is_resolved

    # ── Bulk actions (report list) ──────────────────────────────────

    def _run_bulk(self, request, queryset, fn):
        done, failed = 0, 0
        for report in queryset:
            try:
                fn(report)
                done += 1
            except moderation.ModerationError as exc:
                failed += 1
                self.message_user(request, f"{str(report.id)[:8]}: {exc}", messages.WARNING)
        if done:
            self.message_user(request, f"Done for {done} report(s).", messages.SUCCESS)

    @admin.action(description="Remove the reported content")
    def remove_content_bulk(self, request, queryset):
        self._run_bulk(request, queryset, moderation.remove_content)

    @admin.action(description="Suspend the author of the reported content")
    def suspend_owner_bulk(self, request, queryset):
        self._run_bulk(request, queryset, moderation.suspend_owner)

    @admin.action(description="Dismiss — mark selected reports as resolved")
    def mark_resolved(self, request, queryset):
        count = queryset.update(is_resolved=True)
        self.message_user(request, f"{count} report(s) marked as resolved.")

    @admin.action(description="Reopen — mark selected reports as unresolved")
    def mark_unresolved(self, request, queryset):
        count = queryset.update(is_resolved=False)
        self.message_user(request, f"{count} report(s) marked as unresolved.")

    # ── Buttons on the report page ──────────────────────────────────

    def _change_url(self, object_id):
        return reverse("admin:report_report_change", args=[object_id])

    def _confirm_then(self, request, object_id, title, warning, fn):
        """GET shows a confirmation page; POST (with CSRF) performs the action."""
        if not self.has_change_permission(request):
            raise PermissionDenied
        report = get_object_or_404(Report, pk=object_id)
        if request.method == "POST":
            try:
                self.message_user(request, fn(report), messages.SUCCESS)
            except moderation.ModerationError as exc:
                self.message_user(request, str(exc), messages.ERROR)
            return redirect(self._change_url(object_id))

        context = {
            **self.admin_site.each_context(request),
            "title": title,
            "warning": warning,
            "report": report,
            "summary": moderation.describe_target(report),
            "cancel_url": self._change_url(object_id),
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/report/confirm_moderation.html", context)

    @action(description=_("Remove content"), url_path="remove-content")
    def remove_content_detail(self, request, object_id):
        return self._confirm_then(
            request,
            object_id,
            "Remove the reported content?",
            "The post/comment/story/message is taken down for everyone (a reported "
            "user is suspended instead). All open reports about it are resolved.",
            moderation.remove_content,
        )

    @action(description=_("Suspend author"), url_path="suspend-author")
    def suspend_owner_detail(self, request, object_id):
        return self._confirm_then(
            request,
            object_id,
            "Suspend the author?",
            "Their account is deactivated: they can't sign in or use the app. "
            "You can re-activate them later from the user's admin page.",
            moderation.suspend_owner,
        )

    @action(description=_("Dismiss (no violation)"), url_path="dismiss")
    def dismiss_detail(self, request, object_id):
        if not self.has_change_permission(request):
            raise PermissionDenied
        report = get_object_or_404(Report, pk=object_id)
        count = moderation.resolve_related(report)
        self.message_user(request, f"Dismissed — {count} report(s) resolved.", messages.SUCCESS)
        return redirect(self._change_url(object_id))
