"""
Models for the Story feature: Story, StoryView, StoryReaction, StoryReply.

Design decisions:
- Stories hard-expire after 24 h (expires_at) — no soft-delete needed.
- views_count is denormalized to avoid COUNT(*) on every feed query.
- StoryView uses unique_together to guarantee idempotent view tracking.
- All PKs are UUIDs for global uniqueness across services.
"""

import logging
import os
import uuid

from django.conf import settings
from django.db import models, transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Choices / Enums
# ──────────────────────────────────────────────


class StoryMediaTypeChoices(models.TextChoices):
    TEXT = "TEXT", "Text"
    IMAGE = "IMAGE", "Image"
    VIDEO = "VIDEO", "Video"


class StoryPrivacyChoices(models.TextChoices):
    PUBLIC = "PUBLIC", "Public"
    FRIENDS_ONLY = "FRIENDS_ONLY", "Friends Only"


class StoryReactionTypeChoices(models.TextChoices):
    LIKE = "LIKE", "Like"
    LOVE = "LOVE", "Love"
    HAHA = "HAHA", "Haha"
    SAD = "SAD", "Sad"
    ANGRY = "ANGRY", "Angry"
    WOW = "WOW", "Wow"


# ──────────────────────────────────────────────
# Story
# ──────────────────────────────────────────────


class Story(models.Model):
    """
    A user's story — ephemeral content that expires after 24 hours.

    Media is stored as a URL (uploaded separately or provided externally).
    TEXT stories use text_content + optional bg_color; IMAGE/VIDEO use media_url.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="stories",
        on_delete=models.CASCADE,
    )

    media_type = models.CharField(
        max_length=10,
        choices=StoryMediaTypeChoices.choices,
        default=StoryMediaTypeChoices.TEXT,
    )

    def story_media_path(instance, filename):
        ext = filename.split(".")[-1]
        filename = f"{uuid.uuid4()}.{ext}"
        return os.path.join("stories/media/", filename)

    # Used for IMAGE and VIDEO stories
    media = models.FileField(upload_to=story_media_path, null=True, blank=True)

    # Used for TEXT stories
    text_content = models.TextField(null=True, blank=True)
    bg_color = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        help_text="CSS hex color or gradient token for text story background.",
    )

    privacy = models.CharField(
        max_length=20,
        choices=StoryPrivacyChoices.choices,
        default=StoryPrivacyChoices.PUBLIC,
    )

    # Denormalized to avoid COUNT(*) on every feed read
    views_count = models.PositiveIntegerField(default=0)

    # Set at creation time to now() + STORY_EXPIRY_HOURS from settings
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # Fast lookup of all live stories for a user (profile view)
            models.Index(fields=["author", "expires_at"]),
            # Feed query: filter active stories with specific privacy
            models.Index(fields=["privacy", "expires_at"]),
            # Cleanup / expiry cron job
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self) -> str:
        return f"Story {self.id} by {self.author} ({self.media_type})"

    @property
    def is_active(self) -> bool:
        """Returns True if the story has not yet expired."""
        return timezone.now() < self.expires_at


# ──────────────────────────────────────────────
# Story View (Who Watched)
# ──────────────────────────────────────────────


class StoryView(models.Model):
    """
    Records each unique view of a story.

    unique_together ensures a viewer is counted exactly once,
    making mark-as-viewed idempotent with get_or_create.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    story = models.ForeignKey(Story, related_name="views", on_delete=models.CASCADE)
    viewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="viewed_stories",
        on_delete=models.CASCADE,
    )
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("story", "viewer")
        indexes = [
            # Author fetches the viewers list for their own story
            models.Index(fields=["story", "viewer"]),
            # Viewer's history (e.g. "stories I've seen")
            models.Index(fields=["viewer", "viewed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.viewer} viewed Story {self.story_id}"


# ──────────────────────────────────────────────
# Story Reaction
# ──────────────────────────────────────────────


class StoryReaction(models.Model):
    """
    A user's reaction to a story. One reaction per user per story (upsertable).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    story = models.ForeignKey(
        Story, related_name="reactions", on_delete=models.CASCADE
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="story_reactions",
        on_delete=models.CASCADE,
    )
    reaction_type = models.CharField(
        max_length=10,
        choices=StoryReactionTypeChoices.choices,
        default=StoryReactionTypeChoices.LIKE,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("story", "user")
        indexes = [
            models.Index(fields=["story", "user"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} reacted {self.reaction_type} to Story {self.story_id}"


# ──────────────────────────────────────────────
# Story Reply
# ──────────────────────────────────────────────


class StoryReply(models.Model):
    """
    A direct text reply to a story (like a DM triggered by the story).
    Media replies are out of scope for this version.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    story = models.ForeignKey(Story, related_name="replies", on_delete=models.CASCADE)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="story_replies",
        on_delete=models.CASCADE,
    )
    reply_text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            # Author reads all replies to a story chronologically
            models.Index(fields=["story", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} replied to Story {self.story_id}"


@receiver(post_delete, sender=Story)
def delete_story_media_file(sender, instance, **kwargs):
    """Remove the story's file from storage (Cloudinary) once the row is deleted."""
    if not instance.media:
        return
    storage, name = instance.media.storage, instance.media.name
    transaction.on_commit(lambda: _safe_delete_file(storage, name))


def _safe_delete_file(storage, name):
    try:
        storage.delete(name)
    except Exception:
        logger.exception("Failed to delete story media %s from storage", name)
