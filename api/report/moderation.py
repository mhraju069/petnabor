"""
Acting on reports from the admin: look up the reported item, remove it,
suspend whoever posted it, and resolve every open report about it.

Report.target_type + target_id point at the reported row:
  POST → post.Post            COMMENT → post.PostComment
  STORY → story.Story         MESSAGE → messaging.Message
  BLOG → blog.Blog            USER → users.User
  MEETING → meeting.Meeting
"""

from dataclasses import dataclass, field
from typing import Optional

from django.contrib.auth import get_user_model
from django.db import transaction
from django.urls import NoReverseMatch, reverse

from .models import Report, ReportTargetTypeChoices as T

User = get_user_model()


class ModerationError(Exception):
    """Shown to the moderator as an error message."""


def _model(target_type):
    from api.blog.models import Blog
    from api.meeting.models import Meeting
    from api.messaging.models import Message
    from api.post.models import Post, PostComment
    from api.story.models import Story

    return {
        T.POST: Post,
        T.COMMENT: PostComment,
        T.STORY: Story,
        T.MESSAGE: Message,
        T.BLOG: Blog,
        T.USER: User,
        T.MEETING: Meeting,
    }.get(target_type)


def get_target(report: Report):
    model = _model(report.target_type)
    if not model:
        return None
    manager = getattr(model, "all_objects", None) or model._default_manager
    return manager.filter(pk=report.target_id).first()


def target_owner(report: Report, target=None) -> Optional[User]:
    """Who posted / is responsible for the reported item."""
    target = target if target is not None else get_target(report)
    if target is None:
        return None
    if report.target_type == T.USER:
        return target
    for attr in ("author", "user", "sender"):
        owner = getattr(target, attr, None)
        if owner is not None:
            return owner
    return None


def admin_url(obj) -> Optional[str]:
    if obj is None:
        return None
    try:
        return reverse(f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change", args=[obj.pk])
    except NoReverseMatch:
        return None


@dataclass
class TargetSummary:
    exists: bool
    status: str = ""
    text: str = ""
    media: list = field(default_factory=list)
    owner: Optional[User] = None
    url: Optional[str] = None


def describe_target(report: Report) -> TargetSummary:
    target = get_target(report)
    if target is None:
        return TargetSummary(exists=False, status="Already deleted")

    summary = TargetSummary(exists=True, owner=target_owner(report, target), url=admin_url(target))
    t = report.target_type
    if t == T.POST:
        summary.text = target.content_text or ""
        summary.status = "Removed" if target.is_deleted else "Live"
        summary.media = [m.file.url for m in target.media.all() if m.file]
    elif t == T.COMMENT:
        summary.text = target.comment_text or ""
        summary.status = "Live"
        if target.media_file:
            summary.media = [target.media_file.url]
    elif t == T.STORY:
        summary.text = target.text_content or ""
        summary.status = "Live"
        if target.media:
            summary.media = [target.media.url]
    elif t == T.MESSAGE:
        summary.text = target.text_content or ""
        summary.status = "Deleted for everyone" if target.is_deleted_for_everyone else "Live"
        if target.media_url:
            summary.media = [target.media_url]
    elif t == T.BLOG:
        summary.text = getattr(target, "title", "") or ""
        summary.status = "Removed" if target.is_deleted else ("Published" if target.is_published else "Draft")
    elif t == T.USER:
        name = f"{target.first_name} {target.last_name}".strip() or target.username or target.email
        summary.text = f"{name} ({target.email or target.phone or 'no contact'})"
        summary.status = "Active" if target.is_active else "Suspended"
    elif t == T.MEETING:
        summary.text = getattr(target, "message", "") or getattr(target, "reason", "") or ""
        summary.status = getattr(target, "status", "")
    return summary


def resolve_related(report: Report) -> int:
    """Resolve every open report about the same item (duplicates included)."""
    return Report.objects.filter(
        target_type=report.target_type, target_id=report.target_id, is_resolved=False
    ).update(is_resolved=True)


@transaction.atomic
def remove_content(report: Report) -> str:
    """Take the reported item down. Returns a message for the moderator."""
    t = report.target_type
    if t == T.USER:
        return suspend_owner(report)
    if t == T.MEETING:
        raise ModerationError("Meetings can't be removed; suspend the user instead.")

    target = get_target(report)
    if target is None:
        resolve_related(report)
        return "Already deleted — report resolved."

    if t == T.POST:
        from api.post.services import PostService

        target.is_deleted = True
        target.save(update_fields=["is_deleted", "updated_at"])
        PostService.purge_media(target)  # files removed from storage
    elif t == T.COMMENT:
        from api.post.services import CommentService

        CommentService.delete_comment(target)
    elif t == T.STORY:
        target.delete()  # django-cleanup removes the media file
    elif t == T.MESSAGE:
        target.is_deleted_for_everyone = True
        target.text_content = None
        target.media_url = None
        target.media_file = None
        target.save(
            update_fields=["is_deleted_for_everyone", "text_content", "media_url", "media_file", "updated_at"]
        )
    elif t == T.BLOG:
        target.is_deleted = True
        target.is_published = False
        target.save(update_fields=["is_deleted", "is_published"])
    else:
        raise ModerationError(f"Removing {t} items isn't supported.")

    resolve_related(report)
    return f"{report.get_target_type_display()} removed and report(s) resolved."


@transaction.atomic
def suspend_owner(report: Report) -> str:
    """Deactivate the account behind the reported item (can't sign in or use the API)."""
    owner = target_owner(report)
    if owner is None:
        raise ModerationError("Couldn't find who posted this.")
    if owner.is_staff or owner.is_superuser:
        raise ModerationError("Staff accounts can't be suspended from here.")
    if owner.is_active:
        owner.is_active = False
        owner.save(update_fields=["is_active"])
    resolve_related(report)
    label = owner.email or owner.username or str(owner.pk)
    return f"Suspended {label} and resolved the report(s)."
