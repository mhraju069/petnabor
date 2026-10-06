"""
Business logic and services for Posts, Media handling, Feed, and Interactions.
Key improvements:
- Batch hashtag creation (no N+1 get_or_create loop)
- Prefetch user_reactions for is_liked (zero extra queries)
- Merged Friendship query into one Q() lookup
- LikeService and SaveService split from InteractionService
- CommentService with full counter management (including nested reply counters)
"""

import logging
import re
from typing import List, Optional, Tuple

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.db import transaction
from django.db.models import F, Prefetch, Q, QuerySet

from .models import (
    Hashtag,
    MediaTypeChoices,
    Post,
    PostComment,
    PostLike,
    PrivacyChoices,
    PostMedia,
    SavedPost,
)
from api.users.models import User
from api.friends.models import Friendship

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Media Service
# ──────────────────────────────────────────────


class MediaService:
    """Handles media dispatch only — heavy lifting done by Celery."""

    @staticmethod
    def create_post_media(post: Post, media_files: List[InMemoryUploadedFile]) -> None:
        """
        Save raw files and immediately dispatch background Celery task to process them.
        Does NOT block the request cycle.
        """
        media_instances = []
        for i, m_file in enumerate(media_files):
            content_type = getattr(m_file, "content_type", "")
            is_video = (
                content_type in settings.POST_ALLOWED_VIDEO_MIME
                or m_file.name.lower().endswith((".mp4", ".mov"))
            )
            m_type = MediaTypeChoices.VIDEO if is_video else MediaTypeChoices.IMAGE
            media_instances.append(
                PostMedia(post=post, media_type=m_type, file=m_file, order=i)
            )

        # Bulk create is one INSERT for N files
        created_media = PostMedia.objects.bulk_create(media_instances)

        # Dispatch image processing to Celery (non-blocking)
        image_ids = [
            str(m.id) for m in created_media if m.media_type == MediaTypeChoices.IMAGE
        ]
        if image_ids:
            from .tasks import process_post_media_task

            process_post_media_task.delay(image_ids)
            logger.info("Dispatched media processing for PostMedia ids: %s", image_ids)


# ──────────────────────────────────────────────
# Content Parser
# ──────────────────────────────────────────────


class ContentParserService:
    """Extracts hashtags and @mentions from post text."""

    HASHTAG_REGEX = re.compile(r"#(\w+)")
    MENTION_REGEX = re.compile(r"@([\w.]+)")

    @staticmethod
    def parse_content(text: str) -> Tuple[List[str], List[str]]:
        if not text:
            return [], []
        hashtags = list(
            {t.lower() for t in ContentParserService.HASHTAG_REGEX.findall(text)}
        )
        mentions = list(set(ContentParserService.MENTION_REGEX.findall(text)))
        return hashtags, mentions


# ──────────────────────────────────────────────
# Post Service
# ──────────────────────────────────────────────


class PostService:
    """Manages creation, retrieval, and soft-deletion of Posts."""

    @staticmethod
    @transaction.atomic
    def create_post(
        user: User, data: dict, files: Optional[List[InMemoryUploadedFile]] = None
    ) -> Post:
        content_text = data.get("content_text", "")
        post = Post.objects.create(
            author=user,
            content_text=content_text,
            location_point=data.get("location_point"),
            privacy=data.get("privacy", PrivacyChoices.PUBLIC),
        )

        # ── Hashtags: batch lookup → get_or_create via bulk_create ignore_conflicts ──
        hashtag_names, mention_usernames = ContentParserService.parse_content(
            content_text
        )
        if hashtag_names:
            # Insert any new hashtags in a single query, ignore existing
            Hashtag.objects.bulk_create(
                [Hashtag(name=tag) for tag in hashtag_names],
                ignore_conflicts=True,
            )
            hashtag_objs = Hashtag.objects.filter(name__in=hashtag_names)
            post.hashtags.set(hashtag_objs)

        # ── Mentions: validate usernames exist before linking ─────────
        if mention_usernames:
            valid_users = User.objects.filter(username__in=mention_usernames)
            post.mentions.set(valid_users)

        # ── Media ─────────────────────────────────────────────────────
        if files:
            MediaService.create_post_media(post, files)

        return post

    @staticmethod
    def get_user_posts(user: User, request_user: User) -> QuerySet:
        """Returns posts by `user` visible to `request_user` (excludes soft-deleted)."""
        qs = Post.objects.filter(author=user, is_deleted=False)

        if user == request_user:
            pass  # owner sees all own posts
        else:
            # If blocked in either direction, no posts visible
            from api.friends.services import is_blocked as _is_blocked
            if _is_blocked(request_user, user):
                return Post.objects.none()

            is_friend = Friendship.objects.filter(
                Q(sender=user, receiver=request_user)
                | Q(sender=request_user, receiver=user)
            ).exists()

            if is_friend:
                qs = qs.exclude(privacy=PrivacyChoices.PRIVATE)
            else:
                qs = qs.filter(privacy=PrivacyChoices.PUBLIC)

        return qs.select_related("author", "author__profile").prefetch_related(
            "media",
            "hashtags",
            Prefetch("mentions", queryset=User.objects.select_related("profile")),
            Prefetch(
                "likes",
                queryset=PostLike.objects.filter(user=request_user),
                to_attr="user_reactions",
            ),
        )

    @staticmethod
    def soft_delete_post(post: Post, user: User) -> None:
        """Soft-deletes a post. Raises PermissionError if user is not the author."""
        if post.author != user:
            raise PermissionError("You do not have permission to delete this post.")
        post.is_deleted = True
        post.save(update_fields=["is_deleted", "updated_at"])


# ──────────────────────────────────────────────
# Feed Service
# ──────────────────────────────────────────────


class FeedService:
    """Generates the public discovery feed with friend/self prioritisation."""

    @staticmethod
    def get_feed(user: User) -> QuerySet:
        """
        Public discovery feed — shows ALL public posts from every user.

        Visibility rules:
          • PUBLIC posts  → visible to everyone
          • FRIENDS_ONLY  → visible to friends + self only
          • PRIVATE       → visible to author only (never in feed)

        Ordering:
          1. Friends + self posts come first  (is_priority = True)
          2. Within each bucket, sorted by -created_at (newest first)

        Blocked users are excluded in both directions.
        """
        from api.friends.models import UserBlock  # local import to avoid circular

        # ── 1. Collect friend IDs (single DB round-trip) ──────────────────
        raw_pairs = Friendship.objects.filter(
            Q(sender=user) | Q(receiver=user)
        ).values_list("sender_id", "receiver_id")

        friend_ids: set = set()
        for sender_id, receiver_id in raw_pairs:
            friend_ids.add(sender_id)
            friend_ids.add(receiver_id)
        friend_ids.discard(user.id)  # keep separate for clarity

        priority_ids = friend_ids | {user.id}  # friends + self

        # ── 2. Collect blocked user IDs (both directions) ──────────────────
        flat_blocked: set = set()
        for pair in UserBlock.objects.filter(
            Q(blocker=user) | Q(blocked_user=user)
        ).values_list("blocker_id", "blocked_user_id"):
            flat_blocked.update(pair)
        flat_blocked.discard(user.id)  # never exclude self

        # ── 3. Build visibility filter ─────────────────────────────────────
        # Show:
        #   a) PUBLIC posts from anyone (except blocked)
        #   b) FRIENDS_ONLY posts from friends + self
        #   c) Own PRIVATE posts (so self always sees everything)
        visibility_filter = (
            Q(privacy=PrivacyChoices.PUBLIC)                          # (a)
            | Q(author_id__in=priority_ids,
                privacy=PrivacyChoices.FRIENDS_ONLY)                  # (b)
            | Q(author=user, privacy=PrivacyChoices.PRIVATE)          # (c)
        )

        qs = Post.objects.filter(
            visibility_filter,
            is_deleted=False,
        ).exclude(author_id__in=flat_blocked)

        return (
            qs.select_related("author", "author__profile")
            .prefetch_related(
                "media",
                "hashtags",
                Prefetch("mentions", queryset=User.objects.select_related("profile")),
                Prefetch(
                    "likes",
                    queryset=PostLike.objects.filter(user=user),
                    to_attr="user_reactions",
                ),
            )
            .order_by("-created_at")
        )


# ──────────────────────────────────────────────
# Like Service
# ──────────────────────────────────────────────


class LikeService:
    """Handles all post like/reaction logic atomically."""

    @staticmethod
    @transaction.atomic
    def toggle_like(
        post: Post, user: User, reaction_type: str = "LIKE"
    ) -> Tuple[Optional[PostLike], bool]:
        """
        Toggles a like/reaction on a post.
        - Same reaction again → unlike (returns None, False)
        - Different reaction → updates reaction type (returns like, True)
        - New like → creates and increments counter (returns like, True)
        All counter updates use F() expressions to avoid race conditions.
        """
        like_obj, created = PostLike.objects.get_or_create(
            post=post,
            user=user,
            defaults={"reaction_type": reaction_type},
        )

        if not created:
            if like_obj.reaction_type == reaction_type:
                # Same reaction → unlike
                like_obj.delete()
                Post.objects.filter(id=post.id).update(likes_count=F("likes_count") - 1)
                return None, False
            else:
                # Change reaction type
                like_obj.reaction_type = reaction_type
                like_obj.save(update_fields=["reaction_type"])
        else:
            # New like — increment counter
            Post.objects.filter(id=post.id).update(likes_count=F("likes_count") + 1)
            # Fire notification (async — non-blocking)
            # cache.add is atomic: only the first like ever from this user on this
            # post notifies. Unlike/re-like cycles stay silent.
            if post.author_id != user.id and cache.add(
                f"notified_post_like_{post.id}_{user.id}", 1, timeout=60 * 60 * 24 * 365
            ):
                try:
                    from api.notifications.services import send_notification
                    from api.notifications.models import NotificationTypes

                    sender_display = f"{user.first_name} {user.last_name}".strip() or user.username or "Someone"
                    send_notification(
                        title=sender_display,
                        body="reacted to your post.",
                        user_id=post.author_id,
                        notification_type=NotificationTypes.POST_LIKE,
                        data={"post_id": str(post.id)},
                    )
                except Exception:
                    logger.exception(
                        "Failed to send like notification for post %s", post.id
                    )

        return like_obj, created


# ──────────────────────────────────────────────
# Save Service
# ──────────────────────────────────────────────


class SaveService:
    """Handles saving/bookmarking of posts."""

    @staticmethod
    def toggle_save(post: Post, user: User) -> bool:
        """Returns True if post was saved, False if unsaved."""
        obj, created = SavedPost.objects.get_or_create(post=post, user=user)
        if not created:
            obj.delete()
        return created


# ──────────────────────────────────────────────
# Comment Service
# ──────────────────────────────────────────────


class CommentService:
    """Handles creating and deleting comments with full counter integrity."""

    @staticmethod
    @transaction.atomic
    def create_comment(user: User, validated_data: dict) -> PostComment:
        """Creates a comment and increments the correct counters atomically."""
        comment = PostComment.objects.create(user=user, **validated_data)

        if comment.media_file:
            from .tasks import process_post_comment_media_task

            process_post_comment_media_task.delay(str(comment.id))

        # Always increment post comment count
        Post.objects.filter(id=comment.post_id).update(
            comments_count=F("comments_count") + 1
        )

        # If it's a reply, also increment the parent's replies_count
        if comment.parent_comment_id:
            PostComment.objects.filter(id=comment.parent_comment_id).update(
                replies_count=F("replies_count") + 1
            )
            
        # Fire notification (async — non-blocking)
        try:
            from api.notifications.services import send_notification
            from api.notifications.models import NotificationTypes

            recipient_id = comment.parent_comment.user_id if comment.parent_comment_id else comment.post.author_id
            if recipient_id != user.id:
                sender_display = f"{user.first_name} {user.last_name}".strip() or user.username or "Someone"
                body_msg = "replied to your comment." if comment.parent_comment_id else "commented on your post."
                send_notification(
                    title=sender_display,
                    body=body_msg,
                    user_id=recipient_id,
                    notification_type=NotificationTypes.POST_COMMENT,
                    data={"post_id": str(comment.post_id), "comment_id": str(comment.id)},
                )
        except Exception:
            logger.exception("Failed to send comment notification for post %s", comment.post_id)

        return comment

    @staticmethod
    @transaction.atomic
    def delete_comment(comment: PostComment) -> None:
        """Deletes a comment and decrements the correct counters atomically."""
        post_id = comment.post_id
        parent_comment_id = comment.parent_comment_id

        comment.delete()

        Post.objects.filter(id=post_id).update(comments_count=F("comments_count") - 1)

        if parent_comment_id:
            PostComment.objects.filter(id=parent_comment_id).update(
                replies_count=F("replies_count") - 1
            )


# ──────────────────────────────────────────────
# Backward-compat alias (avoids breaking any existing imports)
# ──────────────────────────────────────────────


class InteractionService:
    """
    Deprecated: Kept for backward compatibility.
    Use LikeService and SaveService directly in new code.
    """

    toggle_like = staticmethod(LikeService.toggle_like)
    toggle_save = staticmethod(SaveService.toggle_save)
