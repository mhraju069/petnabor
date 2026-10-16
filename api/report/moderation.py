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


# Who "owns" each kind of target (also what to preload with it).
_OWNER_FIELD = {
    T.POST: "author",
    T.COMMENT: "user",
    T.STORY: "author",
    T.MESSAGE: "sender",
    T.BLOG: "author",
    T.MEETING: "sender",
}

_MISSING = object()


def _manager(model):
    return getattr(model, "all_objects", None) or model._default_manager


def get_target(report: Report):
    cached = getattr(report, "_target_cache", _MISSING)
    if cached is not _MISSING:
        return cached
    model = _model(report.target_type)
    if not model:
        return None
    qs = _manager(model).filter(pk=report.target_id)
    owner_field = _OWNER_FIELD.get(report.target_type)
    if owner_field:
        qs = qs.select_related(owner_field)
    target = qs.first()
    report._target_cache = target
    return target


def prefetch_targets(reports) -> None:
    """
    Load the reported items (and their authors) for a page of reports with
    one query per target type, instead of several queries per row.
    """
    by_type = {}
    for report in reports:
        by_type.setdefault(report.target_type, []).append(report)
    for target_type, group in by_type.items():
        model = _model(target_type)
        if not model:
            for report in group:
                report._target_cache = None
            continue
        qs = _manager(model).filter(pk__in={r.target_id for r in group})
        owner_field = _OWNER_FIELD.get(target_type)
        if owner_field:
            qs = qs.select_related(owner_field)
        found = {str(obj.pk): obj for obj in qs}
        for report in group:
            report._target_cache = found.get(str(report.target_id))


def target_status(report: Report) -> str:
    """Short status for list columns (no extra queries once prefetched)."""
    target = get_target(report)
    if target is None:
        return "Already deleted"
    t = report.target_type
    if t == T.POST:
        return "Removed" if target.is_deleted else "Live"
    if t == T.MESSAGE:
        return "Deleted for everyone" if target.is_deleted_for_everyone else "Live"
    if t == T.BLOG:
        return "Removed" if target.is_deleted else ("Published" if target.is_published else "Draft")
    if t == T.USER:
        return "Active" if target.is_active else "Suspended"
    if t == T.MEETING:
        return getattr(target, "status", "")
    return "Live"


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


def _delete_files_on_commit(obj, field_names) -> None:
    """
    Remove an object's stored files once the transaction commits. Explicit,
    so it works whether or not django-cleanup is installed (a second delete
    of the same file is harmless).
    """
    names = []
    for field_name in field_names:
        file = getattr(obj, field_name, None)
        if file and getattr(file, "name", None):
            names.append((file.storage, file.name))

    def run():
        for storage, name in names:
            try:
                storage.delete(name)
            except Exception:  # never fail moderation over a storage hiccup
                pass

    if names:
        transaction.on_commit(run)


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
        from api.post.models import PostMedia

        target.is_deleted = True
        target.save(update_fields=["is_deleted", "updated_at"])
        # Take the photos/videos down too (rows + files in storage).
        for media in PostMedia.objects.filter(post=target):
            _delete_files_on_commit(media, ("file", "medium_file", "thumbnail_file"))
            media.delete()
    elif t == T.COMMENT:
        from api.post.services import CommentService

        _delete_files_on_commit(target, ("media_file",))
        CommentService.delete_comment(target)
    elif t == T.STORY:
        _delete_files_on_commit(target, ("media",))
        target.delete()
    elif t == T.MESSAGE:
        _delete_files_on_commit(target, ("media_file",))
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
