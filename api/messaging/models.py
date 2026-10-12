"""
Messaging models — ChatThread, ThreadParticipant, Message.

Schema matches the approved ERD exactly.
Designed for 1M+ users:
  - UUID primary keys throughout
  - DB indexes on all hot query paths
  - Denormalized last_message_text / last_message_timestamp for O(1) inbox ordering
  - Soft-delete via is_deleted_for_everyone (preserves reply references)
"""

import os
import uuid

from django.conf import settings
from django.db import models


def chat_media_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return os.path.join("chat_media/", f"{uuid.uuid4()}.{ext}")


# ──────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────


class ThreadTypes(models.TextChoices):
    DIRECT = "DIRECT", "Direct"
    GROUP = "GROUP", "Group"


class ParticipantRoles(models.TextChoices):
    ADMIN = "ADMIN", "Admin"
    MEMBER = "MEMBER", "Member"


class MessageTypes(models.TextChoices):
    TEXT = "TEXT", "Text"
    IMAGE = "IMAGE", "Image"
    VIDEO = "VIDEO", "Video"
    AUDIO = "AUDIO", "Audio"
    FILE = "FILE", "File"
    SYSTEM = "SYSTEM", "System"
    # Created by the server when someone replies to a story; `metadata`
    # holds a snapshot of the story (it expires, the message doesn't).
    STORY_REPLY = "STORY_REPLY", "Story reply"


# ──────────────────────────────────────────────
# ChatThread
# ──────────────────────────────────────────────


class ChatThread(models.Model):
    """
    A conversation thread — either a 1-to-1 DIRECT or a GROUP chat.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread_type = models.CharField(
        max_length=10,
        choices=ThreadTypes.choices,
        default=ThreadTypes.DIRECT,
        db_index=True,
    )

    # Group-only metadata (null for DIRECT threads)
    name = models.CharField(max_length=255, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    avatar_url = models.URLField(max_length=512, null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_threads",
    )

    # Denormalized for fast inbox ordering — updated on every new message
    last_message_text = models.CharField(max_length=255, null=True, blank=True)
    last_message_timestamp = models.DateTimeField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_message_timestamp"]
        indexes = [
            models.Index(fields=["-last_message_timestamp"]),
            models.Index(fields=["thread_type"]),
        ]

    def __str__(self):
        if self.thread_type == ThreadTypes.GROUP and self.name:
            return f"Group: {self.name}"
        return f"Thread {self.id}"


# ──────────────────────────────────────────────
# ThreadParticipant
# ──────────────────────────────────────────────


class ThreadParticipant(models.Model):
    """
    Explicit through table joining a user to a ChatThread.
    Tracks per-user settings like mute, role, and clear history.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(
        ChatThread,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="thread_memberships",
    )
    role = models.CharField(
        max_length=10,
        choices=ParticipantRoles.choices,
        default=ParticipantRoles.MEMBER,
    )
    is_muted = models.BooleanField(default=False)

    # When the user cleared their visible history — messages before this are hidden
    cleared_history_at = models.DateTimeField(null=True, blank=True)

    joined_at = models.DateTimeField(auto_now_add=True)
    # Soft-leave (track when user left a group; null = still active)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("thread", "user")
        indexes = [
            models.Index(fields=["user", "thread"]),
            models.Index(fields=["thread"]),
        ]

    def __str__(self):
        return f"{self.user} in thread {self.thread_id} ({self.role})"

    @property
    def is_active(self):
        return self.left_at is None


# ──────────────────────────────────────────────
# Message
# ──────────────────────────────────────────────


class Message(models.Model):
    """A single message inside a ChatThread."""

    MAX_TEXT_LENGTH = 2000
    MAX_IMAGE_BYTES = 10 * 1024 * 1024

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(
        ChatThread,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    # Nullable: sender can be null if account is deleted (preserve SYSTEM messages)
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_messages",
    )
    message_type = models.CharField(
        max_length=20,
        choices=MessageTypes.choices,
        default=MessageTypes.TEXT,
        db_index=True,
    )
    text_content = models.TextField(null=True, blank=True)
    media_url = models.URLField(max_length=512, null=True, blank=True)
    # Uploaded attachment (photos sent from the app). `media_url` is filled
    # with its URL so readers only ever need media_url; the file itself is
    # removed from storage by django-cleanup when cleared or deleted.
    media_file = models.FileField(upload_to=chat_media_path, max_length=500, null=True, blank=True)
    # Extra structured data for special message types (e.g. STORY_REPLY:
    # story_id, story_media, story_media_type, story_text, ...).
    metadata = models.JSONField(null=True, blank=True)

    # Reply threading
    reply_to = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replies",
    )

    is_read = models.BooleanField(default=False, db_index=True)
    is_edited = models.BooleanField(default=False)
    # Hard delete visible to everyone (replaces content with placeholder)
    is_deleted_for_everyone = models.BooleanField(default=False, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["thread", "-created_at"]),
            models.Index(fields=["sender"]),
            models.Index(fields=["reply_to"]),
        ]

    def __str__(self):
        if self.is_deleted_for_everyone:
            return f"[{self.sender}] <deleted>"
        preview = (self.text_content or "")[:40] or self.message_type
        return f"[{self.sender}] {preview}"
