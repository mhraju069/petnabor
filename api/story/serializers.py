"""
Serializers for the Story feature.

Key design choices:
- `is_viewed` and `user_reaction` are read from prefetched attributes (zero extra DB hits).
- `StoryCreateSerializer` validates that media_type matches the supplied fields.
- `StoryAuthorSerializer` is a lightweight embed — avoids N+1 from full User serializer.
"""

from django.conf import settings
from rest_framework import serializers

from api.users.models import User
from .models import Story, StoryMediaTypeChoices, StoryReaction, StoryReply, StoryView





# ──────────────────────────────────────────────
# Shared / Nested
# ──────────────────────────────────────────────


class StoryAuthorSerializer(serializers.ModelSerializer):
    """Lightweight user embed — profile_picture sourced from related Profile."""

    profile_picture = serializers.ImageField(
        source="profile.profile_picture", read_only=True
    )

    class Meta:
        model = User
        fields = ["id", "first_name", "last_name", "username", "profile_picture"]


# ──────────────────────────────────────────────
# Story Write
# ──────────────────────────────────────────────


class StoryCreateSerializer(serializers.ModelSerializer):
    """
    Input serializer for story creation.
    Validates that the supplied fields match the chosen media_type:
    - TEXT  → text_content required, media_url forbidden
    - IMAGE/VIDEO → media_url required, text_content ignored
    """

    class Meta:
        model = Story
        fields = [
            "media_type",
            "media",
            "text_content",
            "bg_color",
            "privacy",
        ]

    def validate(self, attrs: dict) -> dict:
        media_type = attrs.get("media_type", StoryMediaTypeChoices.TEXT)

        if media_type == StoryMediaTypeChoices.TEXT:
            if not attrs.get("text_content"):
                raise serializers.ValidationError(
                    {"text_content": "text_content is required for TEXT stories."}
                )
            # Remove irrelevant field
            attrs.pop("media", None)

        else:  # IMAGE or VIDEO
            media_file = attrs.get("media")
            if not media_file:
                raise serializers.ValidationError(
                    {
                        "media": (
                            f"media file is required for {media_type} stories."
                        )
                    }
                )

            # 1. Size check — catch over-limit uploads here instead of letting
            # them reach Cloudinary (which would surface as a 500).
            max_bytes = getattr(
                settings, "STORY_MEDIA_MAX_SIZE_BYTES", 9 * 1024 * 1024
            )
            if media_file.size > max_bytes:
                raise serializers.ValidationError(
                    {
                        "media": (
                            f"'{media_file.name}' exceeds the "
                            f"{max_bytes // (1024 * 1024)} MB size limit."
                        )
                    }
                )

            # 2. Extension check (reuse post rules — same allowed set)
            allowed_ext = getattr(
                settings, "POST_ALLOWED_EXTENSIONS",
                {"jpg", "jpeg", "png", "webp", "gif", "mp4", "mov"},
            )
            name = getattr(media_file, "name", "") or ""
            if "." in name:
                ext = name.rsplit(".", 1)[-1].lower()
                if ext not in allowed_ext:
                    raise serializers.ValidationError(
                        {
                            "media": (
                                f"'{name}' has an unsupported extension ('{ext}')."
                            )
                        }
                    )

            # 3. MIME type check
            allowed_mime = getattr(
                settings,
                "POST_ALLOWED_MIME_TYPES",
                {"image/jpeg", "image/png", "image/webp", "image/gif",
                 "video/mp4", "video/quicktime"},
            )
            content_type = getattr(media_file, "content_type", "") or ""
            if content_type and content_type not in allowed_mime:
                raise serializers.ValidationError(
                    {
                        "media": (
                            f"'{name}' has an unsupported MIME type "
                            f"('{content_type}')."
                        )
                    }
                )

        return attrs


# ──────────────────────────────────────────────
# Story Read
# ──────────────────────────────────────────────


class StoryListSerializer(serializers.ModelSerializer):
    """
    Optimised for feed/list views.

    `is_viewed`    → from prefetched StoryView; no extra DB hit.
    `user_reaction`→ from prefetched `user_reactions` attribute; no extra DB hit.
    """

    author = StoryAuthorSerializer(read_only=True)
    is_viewed = serializers.SerializerMethodField()
    user_reaction = serializers.SerializerMethodField()

    class Meta:
        model = Story
        fields = [
            "id",
            "author",
            "media_type",
            "media",
            "text_content",
            "bg_color",
            "privacy",
            "views_count",
            "is_viewed",
            "user_reaction",
            "expires_at",
            "created_at",
        ]

    def get_is_viewed(self, obj) -> bool:
        """
        Uses the `has_unseen` annotation (added by _annotate_story_queryset)
        when present. Falls back to a DB query if annotation is missing.
        """
        # has_unseen=True means NOT viewed yet; has_unseen=False means viewed
        has_unseen = getattr(obj, "has_unseen", None)
        if has_unseen is not None:
            return not has_unseen

        # Fallback (e.g. single-object retrieve without annotation)
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            return obj.views.filter(viewer=request.user).exists()
        return False

    def get_user_reaction(self, obj) -> str | None:
        """Returns the requesting user's reaction type, or None."""
        user_reactions = getattr(obj, "user_reactions", None)
        if user_reactions is not None:
            return user_reactions[0].reaction_type if user_reactions else None

        request = self.context.get("request")
        if request and request.user.is_authenticated:
            reaction = obj.reactions.filter(user=request.user).first()
            return reaction.reaction_type if reaction else None
        return None


class StoryDetailSerializer(StoryListSerializer):
    """
    Single-story detail view — adds reaction and reply counts.
    Counts are computed from the DB (only used on retrieve, not feed lists).
    """

    reactions_count = serializers.SerializerMethodField()
    replies_count = serializers.SerializerMethodField()

    class Meta(StoryListSerializer.Meta):
        fields = StoryListSerializer.Meta.fields + [
            "reactions_count",
            "replies_count",
        ]

    def get_reactions_count(self, obj) -> int:
        # On retrieve we call .count() once; acceptable for single-object views
        return obj.reactions.count()

    def get_replies_count(self, obj) -> int:
        return obj.replies.count()


# ──────────────────────────────────────────────
# Story View (Viewers list)
# ──────────────────────────────────────────────


class StoryViewSerializer(serializers.ModelSerializer):
    viewer = StoryAuthorSerializer(read_only=True)

    class Meta:
        model = StoryView
        fields = ["id", "viewer", "viewed_at"]


# ──────────────────────────────────────────────
# Story Reaction
# ──────────────────────────────────────────────


class StoryReactionSerializer(serializers.ModelSerializer):
    user = StoryAuthorSerializer(read_only=True)

    class Meta:
        model = StoryReaction
        fields = ["id", "user", "reaction_type", "created_at"]
        read_only_fields = ["id", "user", "created_at"]


class StoryReactionCreateSerializer(serializers.Serializer):
    """Simple input-only serializer for reaction type validation."""

    reaction_type = serializers.ChoiceField(
        choices=StoryReaction._meta.get_field("reaction_type").choices
    )


# ──────────────────────────────────────────────
# Story Reply
# ──────────────────────────────────────────────


class StoryReplySerializer(serializers.ModelSerializer):
    user = StoryAuthorSerializer(read_only=True)

    class Meta:
        model = StoryReply
        fields = ["id", "user", "reply_text", "created_at"]
        read_only_fields = ["id", "user", "created_at"]

    def validate_reply_text(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Reply text cannot be blank.")
        return value


# ──────────────────────────────────────────────
# Story Feed — Grouped
# ──────────────────────────────────────────────


class StoryUserGroupSerializer(serializers.Serializer):
    """Grouped feed entry: one user with all their active stories."""

    user = StoryAuthorSerializer(read_only=True)
    has_unseen = serializers.BooleanField()
    latest_story_at = serializers.DateTimeField()
    stories = StoryListSerializer(many=True, read_only=True)
