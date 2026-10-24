"""
Models for the Post feature: Post, PostMedia, PostLike, PostComment, SavedPost, Hashtag.
Designed with Clean Code principles, UUIDs, and proper indexing for scale.
"""

import os
import uuid
from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.db import models


# ──────────────────────────────────────────────
# Choices & Enums
# ──────────────────────────────────────────────

class PrivacyChoices(models.TextChoices):
    PUBLIC = "PUBLIC", "Public"
    FRIENDS_ONLY = "FRIENDS_ONLY", "Friends Only"
    PRIVATE = "PRIVATE", "Private"


class MediaTypeChoices(models.TextChoices):
    IMAGE = "IMAGE", "Image"
    VIDEO = "VIDEO", "Video"


class MediaProcessingStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    DONE = "DONE", "Done"
    FAILED = "FAILED", "Failed"


class ReactionTypeChoices(models.TextChoices):
    LIKE = "LIKE", "Like"
    LOVE = "LOVE", "Love"
    HAHA = "HAHA", "Haha"
    SAD = "SAD", "Sad"
    ANGRY = "ANGRY", "Angry"


# ──────────────────────────────────────────────
# Helper Functions
# ──────────────────────────────────────────────

def post_media_path(instance, filename):
    """Generate a collision-resistant UUID-based path for post media."""
    _, ext = os.path.splitext(filename)
    unique_filename = f"{uuid.uuid4().hex}{ext.lower()}"
    return os.path.join(f"posts/{instance.post.id}/", unique_filename)


def comment_media_path(instance, filename):
    """Generate a collision-resistant UUID-based path for comment media."""
    _, ext = os.path.splitext(filename)
    unique_filename = f"{uuid.uuid4().hex}{ext.lower()}"
    return os.path.join(f"comments/{instance.id}/", unique_filename)


# ──────────────────────────────────────────────
# Models
# ──────────────────────────────────────────────

class Hashtag(models.Model):
    """Stores unique hashtags for discovery."""
    name = models.CharField(max_length=100, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"#{self.name}"


class Post(models.Model):
    """Core Post model representing user content."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='posts', on_delete=models.CASCADE)

    content_text = models.TextField(null=True, blank=True)
    location_point = gis_models.PointField(srid=4326, null=True, blank=True)

    privacy = models.CharField(max_length=20, choices=PrivacyChoices.choices, default=PrivacyChoices.PUBLIC)

    is_edited = models.BooleanField(default=False)
    # Soft delete — never hard-delete posts, only hide them
    is_deleted = models.BooleanField(default=False, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Discovery relationships (extracted during creation)
    hashtags = models.ManyToManyField(Hashtag, related_name='posts', blank=True)
    mentions = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='post_mentions', blank=True)

    # Denormalized counters to prevent N+1 and heavy Count queries
    likes_count = models.PositiveIntegerField(default=0)
    comments_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['author', 'created_at']),
            models.Index(fields=['privacy', 'created_at']),
            models.Index(fields=['is_deleted', 'created_at']),
        ]

    def __str__(self):
        return f"Post {self.id} by {self.author}"


class PostMedia(models.Model):
    """Media attachments for a post, supporting ordering and async processing status."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, related_name='media', on_delete=models.CASCADE)

    media_type = models.CharField(max_length=10, choices=MediaTypeChoices.choices)
    file = models.FileField(upload_to=post_media_path, max_length=500)
    thumbnail_file = models.FileField(upload_to=post_media_path, max_length=500, null=True, blank=True)
    medium_file = models.FileField(upload_to=post_media_path, max_length=500, null=True, blank=True)

    # Async processing status — frontend polls/uses this to prevent showing broken images
    processing_status = models.CharField(
        max_length=10,
        choices=MediaProcessingStatus.choices,
        default=MediaProcessingStatus.PENDING
    )

    # Order allows sorting multiple media files attached to one post
    order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['post', 'order']
        indexes = [
            models.Index(fields=['post', 'order']),
        ]

    def __str__(self):
        return f"{self.media_type} for Post {self.post_id} (Order: {self.order}, Status: {self.processing_status})"


class PostLike(models.Model):
    """User reactions (likes/loves/etc) to a post."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, related_name='likes', on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='liked_posts', on_delete=models.CASCADE)

    reaction_type = models.CharField(max_length=20, choices=ReactionTypeChoices.choices, default=ReactionTypeChoices.LIKE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('post', 'user')
        indexes = [
            models.Index(fields=['post', 'user']),
        ]

    def __str__(self):
        return f"{self.user} {self.reaction_type} Post {self.post_id}"


class PostComment(models.Model):
    """Comments on a post, supporting nested replies."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, related_name='comments', on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='comments', on_delete=models.CASCADE)

    # Self-referential ForeignKey for nested comments/replies
    parent_comment = models.ForeignKey('self', null=True, blank=True, related_name='replies', on_delete=models.CASCADE)

    comment_text = models.TextField(null=True, blank=True)
    media_file = models.FileField(upload_to=comment_media_path, max_length=500, null=True, blank=True)

    # Friends tagged with @Name in the comment (notified once).
    mentions = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name='comment_mentions', blank=True
    )

    # Denormalized counter for nested replies
    replies_count = models.PositiveIntegerField(default=0)

    is_edited = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['post', 'parent_comment', 'created_at']),
        ]

    def __str__(self):
        return f"Comment by {self.user} on Post {self.post_id}"


class SavedPost(models.Model):
    """Users can save/bookmark posts."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, related_name='saved_by', on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='saved_posts', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('post', 'user')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'created_at']),
        ]

    def __str__(self):
        return f"{self.user} saved Post {self.post_id}"
