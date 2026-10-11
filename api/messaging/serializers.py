"""
Messaging serializers.

DRY, clean, minimal — fields named exactly as model fields.
No business logic here; all validation lives in services.py.
"""

from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import ChatThread, Message, MessageTypes, ThreadParticipant, ThreadTypes

User = get_user_model()


# ──────────────────────────────────────────────
# Minimal nested user representation
# ──────────────────────────────────────────────


class ParticipantUserSerializer(serializers.ModelSerializer):
    """Lightweight user info for embedding inside thread/message responses."""

    avatar = serializers.SerializerMethodField()
    is_online = serializers.SerializerMethodField()
    last_seen = serializers.DateTimeField(source="last_active", read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "avatar", "is_online", "last_seen"]
        read_only_fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "avatar",
            "is_online",
            "last_seen",
        ]

    def get_avatar(self, user):
        profile = getattr(user, "profile", None)
        if profile and profile.profile_picture:
            request = self.context.get("request")
            url = profile.profile_picture.url
            return request.build_absolute_uri(url) if request else url
        return None

    def get_is_online(self, user):
        """Compute online status from last_active (< 5 min = online)."""
        return user.currently_online


# ──────────────────────────────────────────────
# ThreadParticipant
# ──────────────────────────────────────────────


class ThreadParticipantSerializer(serializers.ModelSerializer):
    user = ParticipantUserSerializer(read_only=True)

    class Meta:
        model = ThreadParticipant
        fields = ["id", "user", "role", "is_muted", "joined_at", "left_at"]
        read_only_fields = [
            "id",
            "user",
            "role",
            "is_muted",
            "joined_at",
            "left_at",
        ]


# ──────────────────────────────────────────────
# Message
# ──────────────────────────────────────────────


class ReplyPreviewSerializer(serializers.ModelSerializer):
    """Compact representation of the replied-to message."""

    sender = ParticipantUserSerializer(read_only=True)

    class Meta:
        model = Message
        fields = ["id", "sender", "message_type", "text_content", "media_url"]
        read_only_fields = [
            "id",
            "sender",
            "message_type",
            "text_content",
            "media_url",
        ]


class MessageSerializer(serializers.ModelSerializer):
    sender = ParticipantUserSerializer(read_only=True)
    reply_to = ReplyPreviewSerializer(read_only=True)

    class Meta:
        model = Message
        fields = [
            "id",
            "thread",
            "sender",
            "message_type",
            "text_content",
            "media_url",
            "reply_to",
            "is_read",
            "is_edited",
            "is_deleted_for_everyone",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "sender",
            "thread",
            "is_read",
            "is_edited",
            "is_deleted_for_everyone",
            "created_at",
            "updated_at",
        ]


class SendMessageSerializer(serializers.Serializer):
    """Input serializer for creating a new message."""

    message_type = serializers.ChoiceField(
        choices=MessageTypes.choices,
        default=MessageTypes.TEXT,
    )
    text_content = serializers.CharField(
        max_length=Message.MAX_TEXT_LENGTH,
        required=False,
        allow_blank=False,
    )
    media_url = serializers.URLField(max_length=512, required=False)
    reply_to_id = serializers.UUIDField(required=False)

    def validate(self, data):
        msg_type = data.get("message_type", MessageTypes.TEXT)
        if msg_type == MessageTypes.TEXT and not data.get("text_content"):
            raise serializers.ValidationError(
                {"text_content": "Required for TEXT messages."}
            )
        if msg_type != MessageTypes.TEXT and not data.get("media_url"):
            raise serializers.ValidationError(
                {"media_url": "Required for non-TEXT messages."}
            )
        return data


# ──────────────────────────────────────────────
# ChatThread
# ──────────────────────────────────────────────


class SimpleParticipantSerializer(serializers.ModelSerializer):
    """Lightweight participant for GROUP thread member lists."""

    id = serializers.UUIDField(source="user.id")
    username = serializers.CharField(source="user.username")
    first_name = serializers.CharField(source="user.first_name")
    last_name = serializers.CharField(source="user.last_name")
    is_online = serializers.SerializerMethodField()
    last_seen = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()

    class Meta:
        model = ThreadParticipant
        fields = ["id", "username", "first_name", "last_name", "avatar", "is_online", "last_seen"]

    def get_avatar(self, participant):
        profile = getattr(participant.user, "profile", None)
        if profile and profile.profile_picture:
            request = self.context.get("request")
            url = profile.profile_picture.url
            return request.build_absolute_uri(url) if request else url
        return None

    def get_is_online(self, participant):
        """Compute online status from last_active (< 5 min = online)."""
        return participant.user.currently_online

    def get_last_seen(self, participant):
        last_active = participant.user.last_active
        return last_active.isoformat() if last_active else None


class ChatThreadSerializer(serializers.ModelSerializer):
    """
    Simplified thread serializer.

    DIRECT thread → `other_user` shows the other person (not the requester).
    GROUP  thread → `members` shows a simplified participant list.

    Requires `request` in serializer context to determine current user.
    """

    other_user = serializers.SerializerMethodField()
    members = serializers.SerializerMethodField()
    is_read = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    last_message_text = serializers.SerializerMethodField()
    last_message_timestamp = serializers.SerializerMethodField()

    class Meta:
        model = ChatThread
        fields = [
            "id",
            "thread_type",
            "name",
            "avatar_url",
            "other_user",
            "members",
            "last_message_text",
            "last_message_timestamp",
            "is_read",
            "unread_count",
            "created_at",
        ]

    def _get_all_participants(self, thread):
        """Return all participants, active or left."""
        members = getattr(thread, "all_participants", None)
        if members is None:
            members = thread.participants.select_related("user__profile")
        return members

    def _get_active_participants(self, thread):
        """Return prefetched or freshly queried active participants."""
        members = getattr(thread, "all_participants", None)
        if members is not None:
            return [m for m in members if m.left_at is None]
        return (
            thread.participants.filter(left_at__isnull=True)
            .select_related("user__profile")
        )

    def get_other_user(self, thread):
        """For DIRECT threads: return the participant who is NOT the current user."""
        if thread.thread_type != ThreadTypes.DIRECT:
            return None
        request = self.context.get("request")
        me_id = request.user.id if request else None
        for p in self._get_all_participants(thread):
            if p.user_id != me_id:
                return ParticipantUserSerializer(p.user, context=self.context).data
        return None

    def _get_my_participant(self, thread):
        request = self.context.get("request")
        if not request:
            return None
        me_id = request.user.id
        for p in self._get_all_participants(thread):
            if p.user_id == me_id:
                return p
        return None

    def get_last_message_text(self, thread):
        my_p = self._get_my_participant(thread)
        if my_p and my_p.cleared_history_at and thread.last_message_timestamp:
            if thread.last_message_timestamp <= my_p.cleared_history_at:
                return None
        return thread.last_message_text

    def get_last_message_timestamp(self, thread):
        my_p = self._get_my_participant(thread)
        if my_p and my_p.cleared_history_at and thread.last_message_timestamp:
            if thread.last_message_timestamp <= my_p.cleared_history_at:
                return None
        return thread.last_message_timestamp

    def get_members(self, thread):
        """For GROUP threads: return simplified participant list."""
        if thread.thread_type != ThreadTypes.GROUP:
            return None
        return SimpleParticipantSerializer(
            self._get_active_participants(thread), many=True, context=self.context
        ).data

    def get_unread_count(self, thread):
        """
        Unread messages from others in this thread, for the current user.
        Reads the `unread_count` annotation injected by get_threads_for_user().
        Falls back to a direct DB query if called outside the annotated queryset
        (e.g. single-thread detail view). Cached per thread for get_is_read.
        """
        cache = self.__dict__.setdefault("_unread_cache", {})
        if thread.pk in cache:
            return cache[thread.pk]
        unread = getattr(thread, "unread_count", None)
        if unread is None:
            # Fallback for non-annotated querysets (e.g. thread detail, group create)
            request = self.context.get("request")
            if request:
                from .models import Message
                unread = Message.objects.filter(
                    thread=thread,
                    is_read=False,
                    is_deleted_for_everyone=False,
                ).exclude(sender=request.user).count()
        cache[thread.pk] = unread or 0
        return cache[thread.pk]

    def get_is_read(self, thread):
        """True when the current user has no unread messages from others."""
        return self.get_unread_count(thread) == 0


class CreateDirectThreadSerializer(serializers.Serializer):
    """Input serializer for starting a DIRECT chat."""

    recipient_id = serializers.UUIDField()


class CreateGroupThreadSerializer(serializers.Serializer):
    """Input serializer for creating a GROUP thread."""

    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True)
    avatar_url = serializers.URLField(required=False)
    member_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
class BulkDeleteMessagesSerializer(serializers.Serializer):
    """Input for bulk message deletion."""

    message_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=100,
    )
