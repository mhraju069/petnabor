"""
Business logic for the Story feature.

Service layer principles applied:
- All DB mutations inside @transaction.atomic
- F() expressions for counter updates (race-condition safe)
- No view logic here — views call services only
- Notifications dispatched async via existing send_notification()
"""

import logging
from typing import Tuple

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Prefetch, Q, QuerySet
from django.utils import timezone

from api.friends.models import Friendship
from api.users.models import User
from .models import (
    Story,
    StoryMediaTypeChoices,
    StoryPrivacyChoices,
    StoryReaction,
    StoryReply,
    StoryView,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

STORY_EXPIRY_HOURS: int = getattr(settings, "STORY_EXPIRY_HOURS", 24)


# ──────────────────────────────────────────────────────────────────────────────
# Story Service
# ──────────────────────────────────────────────────────────────────────────────


class StoryService:
    """Handles story creation, deletion, and per-user retrieval."""

    @staticmethod
    @transaction.atomic
    def publish_story(user: User, data: dict) -> Story:
        """
        Creates a new story and sets the expiry timestamp.
        expires_at is always derived server-side to prevent client manipulation.
        """
        from datetime import timedelta

        expiry_delta = timedelta(hours=STORY_EXPIRY_HOURS)
        story = Story.objects.create(
            author=user,
            media_type=data["media_type"],
            media=data.get("media"),
            text_content=data.get("text_content"),
            bg_color=data.get("bg_color"),
            privacy=data.get("privacy", StoryPrivacyChoices.PUBLIC),
            expires_at=timezone.now() + expiry_delta,
        )

        if story.media_type == StoryMediaTypeChoices.IMAGE and story.media:
            from .tasks import process_story_media_task

            # Queue only after the row is committed; a worker that starts
            # first would find no story and skip compression.
            story_id = str(story.id)
            transaction.on_commit(lambda: process_story_media_task.delay(story_id))

        return story

    @staticmethod
    @transaction.atomic
    def delete_story(story: Story, requesting_user: User) -> None:
        """
        Hard-deletes the story. Raises PermissionError if caller isn't the author.
        Stories expire naturally, so soft-delete adds no value here.
        """
        if story.author_id != requesting_user.id:
            raise PermissionError("You do not have permission to delete this story.")
        story.delete()

    @staticmethod
    def get_active_queryset() -> QuerySet:
        """Base queryset: only non-expired stories."""
        return Story.objects.filter(expires_at__gt=timezone.now())

    @staticmethod
    def get_active_stories_for_user(
        target_user: User, requesting_user: User
    ) -> QuerySet:
        """
        Returns target_user's active stories visible to requesting_user.
        - Author sees all their own active stories.
        - If blocked in either direction, returns empty queryset.
        - Friends see PUBLIC + FRIENDS_ONLY stories.
        - Strangers see only PUBLIC stories.
        """
        qs = StoryService.get_active_queryset().filter(author=target_user)

        if target_user == requesting_user:
            # Owner sees all their own stories
            pass
        else:
            # Block check: if blocked in either direction, no stories visible
            from api.friends.services import is_blocked as _is_blocked
            if _is_blocked(requesting_user, target_user):
                return Story.objects.none()

            is_friend = Friendship.objects.filter(
                Q(sender=target_user, receiver=requesting_user)
                | Q(sender=requesting_user, receiver=target_user)
            ).exists()

            if not is_friend:
                qs = qs.filter(privacy=StoryPrivacyChoices.PUBLIC)
            # friends see both PUBLIC and FRIENDS_ONLY (no filter needed)

        return _annotate_story_queryset(qs, requesting_user)


# ──────────────────────────────────────────────────────────────────────────────
# Story Feed Service
# ──────────────────────────────────────────────────────────────────────────────


class StoryFeedService:
    """
    Builds the story feed for the authenticated user.

    Feed algorithm (SQL-native, no ML needed):
    1. Include PUBLIC stories from ALL users (not just friends).
    2. Include FRIENDS_ONLY stories from friends and self.
    3. Exclude expired stories.
    4. Exclude blocked users (both directions).
    5. Order by:
       a. Has unseen story from this author? (unseen-first) — annotated boolean DESC
       b. Most recent story created_at DESC (recency)

    This means users with fresh, unread stories bubble to the top.
    No extra query per story — unseen flag comes from a single Exists subquery.
    """

    @staticmethod
    def get_story_feed(user: User) -> QuerySet:
        from api.friends.models import UserBlock

        # Collect blocked user IDs (both directions) to exclude
        flat_blocked: set = set()
        for pair in UserBlock.objects.filter(
            Q(blocker=user) | Q(blocked_user=user)
        ).values_list("blocker_id", "blocked_user_id"):
            flat_blocked.update(pair)
        flat_blocked.discard(user.id)  # never exclude self

        # Friend IDs (for FRIENDS_ONLY access)
        friend_ids: set = {user.id}
        for sender_id, receiver_id in Friendship.objects.filter(
            Q(sender=user) | Q(receiver=user)
        ).values_list("sender_id", "receiver_id"):
            friend_ids.add(sender_id)
            friend_ids.add(receiver_id)

        # Base: all active stories, excluding blocked users
        # Show PUBLIC stories from everyone + FRIENDS_ONLY stories from friends/self
        qs = (
            StoryService.get_active_queryset()
            .exclude(author_id__in=flat_blocked)
            .filter(
                # Own stories (all privacy levels)
                Q(author=user)
                |
                # Friends' stories (PUBLIC + FRIENDS_ONLY)
                Q(author_id__in=friend_ids)
                |
                # Everyone else's PUBLIC stories
                Q(privacy=StoryPrivacyChoices.PUBLIC)
            )
            .exclude(
                # Exclude FRIENDS_ONLY stories from non-friends (strangers)
                ~Q(author_id__in=friend_ids) & Q(privacy=StoryPrivacyChoices.FRIENDS_ONLY)
            )
        )

        return _annotate_story_queryset(qs, user).order_by(
            "-has_unseen",
            "-created_at",
        )

    @staticmethod
    def get_grouped_story_feed(user: User) -> list:
        """
        Returns stories grouped by author for the feed.
        Each entry: { user, has_unseen, latest_story_at, stories[] }
        Groups with unseen stories come first, then by latest story recency.
        """
        qs = StoryFeedService.get_story_feed(user)

        groups: dict = {}
        for story in qs:
            author_id = story.author_id
            if author_id not in groups:
                groups[author_id] = {
                    "user": story.author,
                    "has_unseen": False,
                    "latest_story_at": story.created_at,
                    "stories": [],
                }
            group = groups[author_id]
            group["stories"].append(story)
            if getattr(story, "has_unseen", False):
                group["has_unseen"] = True
            if story.created_at > group["latest_story_at"]:
                group["latest_story_at"] = story.created_at

        return sorted(
            groups.values(),
            key=lambda g: (not g["has_unseen"], -g["latest_story_at"].timestamp()),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Story View Service
# ──────────────────────────────────────────────────────────────────────────────


class StoryViewService:
    """Tracks who viewed a story and exposes the viewers list to the author."""

    @staticmethod
    @transaction.atomic
    def mark_as_viewed(story: Story, viewer: User) -> bool:
        """
        Records a single unique view per (story, viewer) pair.
        Increments views_count via F() only on the first view (race-safe).
        Returns True if this was a new view, False if already viewed.
        """
        _, created = StoryView.objects.get_or_create(
            story=story,
            viewer=viewer,
        )
        if created:
            Story.objects.filter(id=story.id).update(
                views_count=F("views_count") + 1
            )
        return created

    @staticmethod
    def get_story_viewers(story: Story, requesting_user: User) -> QuerySet:
        """
        Returns the viewers list. Only the story's author may access this.
        Raises PermissionError for any other caller.
        """
        if story.author_id != requesting_user.id:
            raise PermissionError("Only the story author can view the viewers list.")

        return (
            StoryView.objects.filter(story=story)
            .select_related("viewer", "viewer__profile")
            .order_by("-viewed_at")
        )


# ──────────────────────────────────────────────────────────────────────────────
# Story Interaction Service
# ──────────────────────────────────────────────────────────────────────────────


class StoryInteractionService:
    """Handles reactions and replies to stories."""

    @staticmethod
    @transaction.atomic
    def react_to_story(
        story: Story, user: User, reaction_type: str
    ) -> Tuple[StoryReaction, bool]:
        """
        Upserts a reaction on a story.
        - New reaction → created=True
        - Changed reaction type → updates in place, created=False
        Returns (reaction_obj, created).
        """
        reaction, created = StoryReaction.objects.get_or_create(
            story=story,
            user=user,
            defaults={"reaction_type": reaction_type},
        )
        if not created and reaction.reaction_type != reaction_type:
            reaction.reaction_type = reaction_type
            reaction.save(update_fields=["reaction_type"])

        # Notify story author of the reaction (non-blocking via Celery)
        if created and story.author_id != user.id:
            _notify_story_author_of_reaction(story, user)

        return reaction, created

    @staticmethod
    @transaction.atomic
    def remove_reaction(story: Story, user: User) -> bool:
        """
        Removes the user's reaction from a story.
        Returns True if a reaction was deleted, False if none existed.
        """
        deleted_count, _ = StoryReaction.objects.filter(
            story=story, user=user
        ).delete()
        return deleted_count > 0

    @staticmethod
    def reply_to_story(story: Story, user: User, reply_text: str) -> StoryReply:
        """
        Creates a text reply to a story and delivers it to the author's chat
        as a STORY_REPLY message (which also sends the chat push).

        The reply is saved even if the chat message can't be created; in that
        case the author still gets the story-reply notification.
        """
        reply = StoryReply.objects.create(
            story=story,
            user=user,
            reply_text=reply_text,
        )

        if story.author_id != user.id:
            if not _send_story_reply_message(story, user, reply_text):
                _notify_story_author_of_reply(story, user)

        return reply

    @staticmethod
    def get_story_replies(story: Story, requesting_user: User) -> QuerySet:
        """
        Returns replies for a story.
        Only the story author or the replier can see each reply.
        For simplicity, author sees all; others only see their own.
        """
        qs = StoryReply.objects.filter(story=story).select_related(
            "user", "user__profile"
        )
        if story.author_id == requesting_user.id:
            return qs
        return qs.filter(user=requesting_user)


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers (not part of the public service API)
# ──────────────────────────────────────────────────────────────────────────────


def _annotate_story_queryset(qs: QuerySet, requesting_user: User) -> QuerySet:
    """
    Applies standard join/prefetch optimisations and the `has_unseen` annotation.

    has_unseen: True when requesting_user has NOT viewed a story yet.
    Used by the feed ordering with zero extra queries (single Exists subquery).
    """
    already_viewed_subquery = StoryView.objects.filter(
        story=OuterRef("pk"),
        viewer=requesting_user,
    )
    return (
        qs.select_related("author", "author__profile")
        .prefetch_related(
            # Prefetch only the requesting user's reaction to avoid N+1
            Prefetch(
                "reactions",
                queryset=StoryReaction.objects.filter(user=requesting_user),
                to_attr="user_reactions",
            )
        )
        .annotate(has_unseen=~Exists(already_viewed_subquery))
    )


def story_snapshot(story: Story) -> dict:
    """What a chat message needs to show the story after it has expired."""
    media_url = None
    if story.media:
        try:
            media_url = story.media.url
        except Exception:
            media_url = None
    return {
        "story_id": str(story.id),
        "story_author_id": str(story.author_id),
        "story_media_type": story.media_type,
        "story_media": media_url,
        "story_text": (story.text_content or "")[:200] or None,
        "story_bg_color": story.bg_color or None,
        "story_expires_at": story.expires_at.isoformat() if story.expires_at else None,
    }


def _send_story_reply_message(story: Story, replier: User, reply_text: str) -> bool:
    """
    Posts the reply into the direct chat between replier and story author.
    Returns False (and logs) if it couldn't be delivered.
    """
    try:
        from api.messaging.models import MessageTypes
        from api.messaging.services import get_or_create_direct_thread, send_message

        thread, _ = get_or_create_direct_thread(replier, story.author)
        send_message(
            sender=replier,
            thread_id=thread.id,
            text_content=reply_text,
            message_type=MessageTypes.STORY_REPLY,
            metadata=story_snapshot(story),
            # Seeing the story already passed its privacy and block checks.
            enforce_permission=False,
        )
        return True
    except Exception:
        logger.exception("Failed to post story reply %s to chat", story.id)
        return False


def _notify_story_author_of_reaction(story: Story, reactor: User) -> None:
    """Fires a non-blocking push notification to the story author."""
    try:
        from api.notifications.services import send_notification
        from api.notifications.models import NotificationTypes

        sender_display = f"{reactor.first_name} {reactor.last_name}".strip() or reactor.username or "Someone"
        send_notification(
            title=sender_display,
            body="reacted to your story.",
            user_id=story.author_id,
            notification_type=NotificationTypes.STORY_LIKE,
            data={"story_id": str(story.id)},
        )
    except Exception:
        logger.exception(
            "Failed to send story reaction notification for story %s", story.id
        )


def _notify_story_author_of_reply(story: Story, replier: User) -> None:
    """Fires a non-blocking push notification to the story author."""
    try:
        from api.notifications.services import send_notification
        from api.notifications.models import NotificationTypes

        sender_display = f"{replier.first_name} {replier.last_name}".strip() or replier.username or "Someone"
        send_notification(
            title=sender_display,
            body="replied to your story.",
            user_id=story.author_id,
            notification_type=NotificationTypes.STORY_COMMENT,
            data={"story_id": str(story.id)},
        )
    except Exception:
        logger.exception(
            "Failed to send story reply notification for story %s", story.id
        )
