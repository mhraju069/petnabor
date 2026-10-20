"""
Messaging service layer — all business logic, zero logic in views.

ERD schema: ChatThread, ThreadParticipant, Message.

Key behaviours:
- can_message() is Redis-cached for 60s; invalidated on block/friendship changes.
- Only friends can START a new thread; once a thread exists, ex-friends may continue.
- Blocked users are always denied.
- All DB reads use select_related / prefetch_related (no N+1).
- All errors raise DRF exceptions (handled by custom_exception_handler).
"""

import logging

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, Prefetch, Q, Subquery
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from api.friends.models import Friendship, UserBlock

from .models import ChatThread, Message, MessageTypes, ParticipantRoles, ThreadParticipant, ThreadTypes

logger = logging.getLogger(__name__)
User = get_user_model()


# ──────────────────────────────────────────────
# Cache helpers
# ──────────────────────────────────────────────

PERMISSION_CACHE_TTL = 60  # seconds


def _permission_cache_key(user_a_id, user_b_id):
    """Canonical cache key — sorted so argument order never matters."""
    lo, hi = sorted([str(user_a_id), str(user_b_id)])
    return f"can_msg:{lo}:{hi}"


def invalidate_messaging_permission(user_a_id, user_b_id):
    """
    Invalidate the can_message cache for a pair of users.
    Must be called whenever friendship or block status changes.
    """
    cache.delete(_permission_cache_key(user_a_id, user_b_id))


def invalidate_inbox_cache(user_id):
    """Invalidate the first-page inbox cache and the WS thread-list cache."""
    cache.delete(f"user_inbox_{user_id}_page_1")
    cache.delete(f"ws_threads_{user_id}")


# ──────────────────────────────────────────────
# Realtime helpers
# ──────────────────────────────────────────────


def message_preview(message_type, text_content):
    """The inbox line for a message (also stored on the thread)."""
    if message_type == MessageTypes.STORY_REPLY:
        return f"Story reply: {text_content}"[:255]
    if message_type == MessageTypes.IMAGE:
        return (f"📷 {text_content}" if text_content else "📷 Photo")[:255]
    return (text_content or message_type)[:255]


def _active_participant_ids(thread_id):
    return list(
        ThreadParticipant.objects.filter(thread_id=thread_id, left_at__isnull=True)
        .values_list("user_id", flat=True)
    )


def _broadcast(user_ids, event):
    """Send one channel-layer event to each user's personal group. Never raises."""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        send = async_to_sync(channel_layer.group_send)
        for user_id in user_ids:
            send(f"user_{user_id}", event)
    except Exception:
        logger.exception("Failed to broadcast WebSocket event %s", event.get("type"))


def _refresh_thread_preview(thread_id):
    """Point the thread's stored preview at its latest visible message."""
    latest = (
        Message.objects.filter(thread_id=thread_id, is_deleted_for_everyone=False)
        .only("message_type", "text_content", "created_at")
        .order_by("-created_at")
        .first()
    )
    ChatThread.objects.filter(id=thread_id).update(
        last_message_text=message_preview(latest.message_type, latest.text_content) if latest else None,
        **({"last_message_timestamp": latest.created_at} if latest else {}),
    )


# ──────────────────────────────────────────────
# Permission check
# ──────────────────────────────────────────────


def can_message(sender, recipient):
    """
    Rules (priority order):
    1. Either party blocked the other → DENY.
    2. A DIRECT thread already exists between them → ALLOW
       (conversation was started while they were friends).
    3. They are currently friends → ALLOW.
    4. Otherwise → DENY.

    Result cached in Redis for PERMISSION_CACHE_TTL seconds.
    """
    cache_key = _permission_cache_key(sender.id, recipient.id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Rule 1 — block check (either direction)
    is_blocked = UserBlock.objects.filter(
        Q(blocker=sender, blocked_user=recipient)
        | Q(blocker=recipient, blocked_user=sender)
    ).exists()
    if is_blocked:
        cache.set(cache_key, False, PERMISSION_CACHE_TTL)
        return False

    # Rule 2 — prior direct thread exists
    has_prior_thread = (
        ThreadParticipant.objects.filter(user=sender)
        .filter(thread__participants__user=recipient, thread__thread_type=ThreadTypes.DIRECT)
        .exists()
    )
    if has_prior_thread:
        cache.set(cache_key, True, PERMISSION_CACHE_TTL)
        return True

    # Rule 3 — currently friends
    are_friends = Friendship.objects.filter(
        Q(sender=sender, receiver=recipient)
        | Q(sender=recipient, receiver=sender)
    ).exists()

    result = are_friends
    cache.set(cache_key, result, PERMISSION_CACHE_TTL)
    return result


# ──────────────────────────────────────────────
# Thread management
# ──────────────────────────────────────────────


def get_or_create_direct_thread(user_a, user_b):
    """
    Return the existing DIRECT thread between user_a and user_b,
    or create one atomically.
    """
    existing = (
        ChatThread.objects.filter(
            thread_type=ThreadTypes.DIRECT,
            participants__user=user_a,
        )
        .filter(participants__user=user_b)
        .first()
    )
    if existing:
        # Reactivate user_a so they can see/send messages in it again
        ThreadParticipant.objects.filter(thread=existing, user=user_a).update(left_at=None)
        invalidate_inbox_cache(user_a.id)
        return existing, False

    with transaction.atomic():
        thread = ChatThread.objects.create(
            thread_type=ThreadTypes.DIRECT,
            created_by=user_a,
        )
        ThreadParticipant.objects.bulk_create([
            ThreadParticipant(thread=thread, user=user_a, role=ParticipantRoles.MEMBER),
            ThreadParticipant(thread=thread, user=user_b, role=ParticipantRoles.MEMBER),
        ])
    invalidate_inbox_cache(user_a.id)
    invalidate_inbox_cache(user_b.id)
    return thread, True


def create_group_thread(creator, name, description=None, avatar_url=None, member_ids=None):
    """
    Create a new GROUP thread with the creator as ADMIN.
    """
    if not name:
        raise ValidationError("Group name is required.")

    member_ids = member_ids or []
    members = list(User.objects.filter(id__in=member_ids, is_active=True))

    with transaction.atomic():
        thread = ChatThread.objects.create(
            thread_type=ThreadTypes.GROUP,
            name=name,
            description=description,
            avatar_url=avatar_url,
            created_by=creator,
        )
        participants = [
            ThreadParticipant(thread=thread, user=creator, role=ParticipantRoles.ADMIN)
        ]
        for member in members:
            if member.id != creator.id:
                participants.append(
                    ThreadParticipant(thread=thread, user=member, role=ParticipantRoles.MEMBER)
                )
        ThreadParticipant.objects.bulk_create(participants)
    for p in participants:
        invalidate_inbox_cache(p.user_id)
    return thread


def get_thread_for_participant(user, thread_id):
    """
    Fetch a ChatThread by ID, ensuring `user` is an active participant.
    Raises NotFound if not a member or thread doesn't exist.
    """
    membership = (
        ThreadParticipant.objects.select_related("thread")
        .filter(user=user, thread_id=thread_id, left_at__isnull=True)
        .first()
    )
    if not membership:
        raise NotFound("Thread not found.")
    return membership.thread, membership


# ──────────────────────────────────────────────
# Inbox
# ──────────────────────────────────────────────


def get_threads_for_user(user):
    """
    Return all active threads for `user`, most recent first.
    Uses prefetch_related for participants — no N+1.

    Annotates each thread with `unread_count`: the number of unread messages
    in that thread that were NOT sent by the current user.
    This lets the serializer expose `is_read` at zero extra cost.
    """
    active_thread_ids = ThreadParticipant.objects.filter(
        user=user, left_at__isnull=True
    ).values_list("thread_id", flat=True)

    # Subquery: count unread messages in each thread not sent by the current user
    unread_subquery = Subquery(
        Message.objects.filter(
            thread=OuterRef("pk"),
            is_read=False,
            is_deleted_for_everyone=False,
        ).exclude(sender=user)
        .values("thread")
        .annotate(cnt=Count("id"))
        .values("cnt"),
        output_field=IntegerField(),
    )

    return (
        ChatThread.objects.filter(id__in=active_thread_ids)
        .prefetch_related(
            Prefetch(
                "participants",
                queryset=ThreadParticipant.objects.select_related("user__profile"),
                to_attr="all_participants",
            ),
        )
        .annotate(unread_count=unread_subquery)
        .order_by("-last_message_timestamp")
    )


# ──────────────────────────────────────────────
# Messages
# ──────────────────────────────────────────────


def get_messages_in_thread(user, thread_id):
    """
    Return visible messages in a thread for `user`.
    - Verifies membership.
    - Hides messages before cleared_history_at.
    - Hides is_deleted_for_everyone messages.
    """
    thread, membership = get_thread_for_participant(user, thread_id)

    qs = (
        Message.objects.select_related("sender__profile", "reply_to__sender__profile")
        .filter(thread_id=thread_id, is_deleted_for_everyone=False)
        .order_by("-created_at")
    )

    if membership.cleared_history_at:
        qs = qs.filter(created_at__gte=membership.cleared_history_at)

    return qs


@transaction.atomic
def send_message(
    sender,
    thread_id,
    text_content=None,
    message_type=MessageTypes.TEXT,
    media_url=None,
    reply_to_id=None,
    metadata=None,
    enforce_permission=True,
    media_file=None,
):
    """
    Send a message in a thread.

    1. Verify sender is an active participant.
    2. Re-check can_message permission for DIRECT threads (fast, cached).
    3. Validate content.
    4. Create Message.
    5. Update thread denormalized fields.
    6. Dispatch async push notification.

    `enforce_permission=False` skips the friends/prior-thread rule (blocks
    are still enforced) — used for server-created messages such as story
    replies, where viewing the story already passed the privacy checks.

    Returns the newly created Message.
    """
    thread, _ = get_thread_for_participant(sender, thread_id)

    # For DIRECT threads, re-check messaging permission
    if thread.thread_type == ThreadTypes.DIRECT:
        other = (
            ThreadParticipant.objects.select_related("user")
            .filter(thread=thread, left_at__isnull=True)
            .exclude(user=sender)
            .first()
        )
        if other:
            if enforce_permission:
                allowed = can_message(sender, other.user)
            else:
                allowed = not UserBlock.objects.filter(
                    Q(blocker=sender, blocked_user=other.user)
                    | Q(blocker=other.user, blocked_user=sender)
                ).exists()
            if not allowed:
                raise PermissionDenied("You are not allowed to send messages to this user.")

    # Validate content
    if message_type in (MessageTypes.TEXT, MessageTypes.STORY_REPLY):
        if not text_content or not text_content.strip():
            raise ValidationError("Text content cannot be empty.")
        text_content = text_content.strip()
        if len(text_content) > Message.MAX_TEXT_LENGTH:
            raise ValidationError(f"Message cannot exceed {Message.MAX_TEXT_LENGTH} characters.")
    else:
        if not media_url and not media_file:
            raise ValidationError("A photo (or media_url) is required for non-text messages.")
        # Optional caption on photos.
        text_content = (text_content or "").strip() or None
        if text_content and len(text_content) > Message.MAX_TEXT_LENGTH:
            raise ValidationError(f"Caption cannot exceed {Message.MAX_TEXT_LENGTH} characters.")

    # Validate reply_to
    reply_to = None
    if reply_to_id:
        reply_to = Message.objects.filter(id=reply_to_id, thread_id=thread_id).first()
        if not reply_to:
            raise ValidationError("Replied-to message not found in this thread.")

    message = Message.objects.create(
        thread=thread,
        sender=sender,
        message_type=message_type,
        text_content=text_content,
        media_url=media_url,
        reply_to=reply_to,
        metadata=metadata,
        media_file=media_file,
    )
    if media_file:
        # Readers (serializer, WebSocket, app) only use media_url.
        message.media_url = message.media_file.url
        message.save(update_fields=["media_url"])

    # Update denormalized inbox fields
    preview = message_preview(message_type, text_content)
    ChatThread.objects.filter(id=thread.id).update(
        last_message_text=preview,
        last_message_timestamp=message.created_at,
    )

    # For DIRECT threads, reactivate any participant who left so they receive the thread
    if thread.thread_type == ThreadTypes.DIRECT:
        ThreadParticipant.objects.filter(thread=thread, left_at__isnull=False).update(left_at=None)

    # Push + realtime only once the message is committed: before that, a
    # receiver refetching (or the push task) could miss it, and the inbox
    # list rebuilt for the socket was cached without it.
    transaction.on_commit(lambda: _after_message_sent(message, thread, sender, preview))

    return message


def _after_message_sent(message, thread, sender, preview):
    """Push notification + WebSocket delivery for a committed message."""
    participant_ids = _active_participant_ids(thread.id)

    # Async push notification (FCM). Muted participants still get the
    # message, just no push.
    try:
        from .tasks import notify_new_message

        muted = set(
            ThreadParticipant.objects.filter(thread=thread, left_at__isnull=True, is_muted=True)
            .values_list("user_id", flat=True)
        )
        recipient_ids = [uid for uid in participant_ids if uid != sender.id and uid not in muted]
        if recipient_ids:
            notify_new_message.delay(
                str(message.id),
                str(thread.id),
                str(sender.id),
                [str(uid) for uid in recipient_ids],
                preview[:100],
            )
    except Exception:
        logger.exception("Failed to queue messaging push notification")

    try:
        from api.notifications.services import avatar_url

        # Plain-dict payload: all UUIDs as str so channels_redis/msgpack can
        # serialize it.
        ws_payload = {
            "id": str(message.id),
            "thread": str(thread.id),
            "sender": {
                "id": str(sender.id),
                "username": sender.username,
                "first_name": sender.first_name,
                "last_name": sender.last_name,
                "avatar": avatar_url(sender) or None,
            },
            "message_type": message.message_type,
            "text_content": message.text_content,
            "media_url": message.media_url,
            "metadata": message.metadata,
            "reply_to": None,
            "is_read": message.is_read,
            "is_edited": False,
            "is_deleted_for_everyone": False,
            "created_at": message.created_at.isoformat(),
            "updated_at": message.updated_at.isoformat(),
            # Lets the app update its inbox row without refetching the list.
            "thread_preview": preview,
        }
        if message.reply_to:
            reply_sender = message.reply_to.sender
            ws_payload["reply_to"] = {
                "id": str(message.reply_to.id),
                "sender": {
                    "id": str(reply_sender.id),
                    "username": reply_sender.username,
                    "first_name": reply_sender.first_name,
                    "last_name": reply_sender.last_name,
                    "avatar": None,
                } if reply_sender else None,
                "text_content": message.reply_to.text_content,
                "media_url": message.reply_to.media_url,
                "message_type": message.reply_to.message_type,
            }
    except Exception:
        logger.exception("Failed to build WebSocket payload for message %s", message.id)
        return

    for p_id in participant_ids:
        invalidate_inbox_cache(p_id)
    _broadcast(participant_ids, {"type": "chat_message", "message": ws_payload})
    # Older app versions rebuild their inbox from a pushed list; newer ones
    # apply `thread_preview` themselves (the consumer skips this for them).
    _broadcast(participant_ids, {"type": "thread_list_update", "reason": "new_message"})


def mark_messages_read(user, thread_id):
    """
    Mark all unread messages in a thread as read for this user, and tell the
    other participants (read receipts). Returns how many were marked.
    """
    get_thread_for_participant(user, thread_id)  # verify membership
    updated = Message.objects.filter(
        thread_id=thread_id,
        is_read=False,
        is_deleted_for_everyone=False,
    ).exclude(sender=user).update(is_read=True)

    if updated:
        # The cached inbox still had this chat's unread count.
        invalidate_inbox_cache(user.id)
        others = [uid for uid in _active_participant_ids(thread_id) if uid != user.id]
        transaction.on_commit(lambda: _broadcast(others, {
            "type": "message_read",
            "thread_id": str(thread_id),
            "reader_id": str(user.id),
        }))
    return updated


def _after_messages_deleted(thread_id, message_ids):
    """Deleted for everyone: fix the inbox preview and tell everyone live."""
    _refresh_thread_preview(thread_id)
    participant_ids = _active_participant_ids(thread_id)
    for p_id in participant_ids:
        invalidate_inbox_cache(p_id)
    transaction.on_commit(lambda: _broadcast(participant_ids, {
        "type": "message_deleted",
        "thread_id": str(thread_id),
        "message_ids": [str(m) for m in message_ids],
    }))


def delete_message_for_everyone(user, thread_id, message_id):
    """
    Delete a message for everyone (sender only).
    Sets is_deleted_for_everyone=True and clears text/media.
    """
    get_thread_for_participant(user, thread_id)

    message = Message.objects.filter(id=message_id, thread_id=thread_id).first()
    if not message:
        raise NotFound("Message not found.")
    if message.sender_id != user.id:
        raise PermissionDenied("You can only delete your own messages.")

    message.is_deleted_for_everyone = True
    message.text_content = None
    message.media_url = None
    message.media_file = None  # django-cleanup deletes the file after commit
    message.save(
        update_fields=["is_deleted_for_everyone", "text_content", "media_url", "media_file", "updated_at"]
    )
    _after_messages_deleted(thread_id, [message.id])
    return message


@transaction.atomic
def delete_messages_bulk(user, thread_id, message_ids):
    """
    Soft-delete multiple messages for everyone (sender only).
    """
    get_thread_for_participant(user, thread_id)  # verify membership

    messages = Message.objects.filter(
        thread_id=thread_id,
        id__in=message_ids,
        sender=user,
        is_deleted_for_everyone=False,
    )

    # Photos are cleared per instance so django-cleanup removes their files
    # (queryset.update() skips model signals).
    for message in messages.exclude(media_file="").exclude(media_file__isnull=True):
        message.media_file = None
        message.save(update_fields=["media_file"])

    deleted_ids = list(messages.values_list("id", flat=True))
    count = messages.update(
        is_deleted_for_everyone=True,
        text_content=None,
        media_url=None,
        updated_at=timezone.now(),
    )
    if deleted_ids:
        _after_messages_deleted(thread_id, deleted_ids)
    return count


def leave_thread(user, thread_id):
    """
    User leaves a thread (marks left_at and clears history).
    """
    _, membership = get_thread_for_participant(user, thread_id)
    now = timezone.now()
    membership.left_at = now
    membership.cleared_history_at = now
    membership.save(update_fields=["left_at", "cleared_history_at"])
    invalidate_inbox_cache(user.id)
    return True


def set_thread_muted(user, thread_id, muted: bool):
    """
    Mute or unmute a thread for this user only. Muted threads still receive
    messages (and unread counts) but send this user no push notifications.
    """
    _, membership = get_thread_for_participant(user, thread_id)
    if membership.is_muted != muted:
        membership.is_muted = muted
        membership.save(update_fields=["is_muted"])
        invalidate_inbox_cache(user.id)
    return membership


def clear_thread_history(user, thread_id):
    """Per-user clear history — sets cleared_history_at to now."""
    _, membership = get_thread_for_participant(user, thread_id)
    membership.cleared_history_at = timezone.now()
    membership.save(update_fields=["cleared_history_at"])

    # Invalidate cache so the next inbox fetch correctly calculates last_message as None
    invalidate_inbox_cache(user.id)

    # Tell this user's other open apps/devices to refresh their inbox.
    _broadcast([user.id], {"type": "thread_list_update"})


@transaction.atomic
def delete_thread_for_everyone(user, thread_id):
    """
    Delete a thread and all messages for everyone.
    For GROUP: Only ADMIN can delete.
    For DIRECT: Either participant can delete (hard delete).
    """
    thread, membership = get_thread_for_participant(user, thread_id)

    if thread.thread_type == ThreadTypes.GROUP:
        if membership.role != ParticipantRoles.ADMIN:
            raise PermissionDenied("Only admins can delete a group thread.")
    
    # Hard delete the thread (and related participants/messages via cascade)
    participant_ids = list(thread.participants.values_list("user_id", flat=True))
    thread.delete()
    for pid in participant_ids:
        invalidate_inbox_cache(pid)
    return True
