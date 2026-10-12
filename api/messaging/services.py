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
        Message.objects.select_related("sender__profile", "reply_to__sender")
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
        if not media_url:
            raise ValidationError("media_url is required for non-text messages.")

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
    )

    # Update denormalized inbox fields
    if message_type == MessageTypes.STORY_REPLY:
        preview = f"Story reply: {text_content}"[:255]
    else:
        preview = (text_content or message_type)[:255]
    ChatThread.objects.filter(id=thread.id).update(
        last_message_text=preview,
        last_message_timestamp=message.created_at,
    )

    # For DIRECT threads, reactivate any participant who left so they receive the thread
    if thread.thread_type == ThreadTypes.DIRECT:
        ThreadParticipant.objects.filter(thread=thread, left_at__isnull=False).update(left_at=None)

    # Async push notification (FCM)
    try:
        from .tasks import notify_new_message
        recipient_ids = list(
            ThreadParticipant.objects.filter(thread=thread, left_at__isnull=True)
            .exclude(user=sender)
            .values_list("user_id", flat=True)
        )
        notify_new_message.delay(
            str(message.id),
            str(thread.id),
            str(sender.id),
            [str(uid) for uid in recipient_ids],
            preview[:100],
        )
    except Exception:
        logger.exception("Failed to queue messaging push notification")

    # Real-time WebSocket broadcast
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()

        # Build a plain-dict payload — all UUIDs converted to str so
        # channels_redis / msgpack can serialize without error.
        ws_payload = {
            "id": str(message.id),
            "thread": str(thread.id),
            "sender": {
                "id": str(message.sender.id),
                "username": message.sender.username,
                "first_name": message.sender.first_name,
                "last_name": message.sender.last_name,
            },
            "message_type": message.message_type,
            "text_content": message.text_content,
            "media_url": message.media_url,
            "metadata": message.metadata,
            "reply_to": None,
            "is_read": message.is_read,
            "created_at": message.created_at.isoformat(),
        }

        if message.reply_to:
            ws_payload["reply_to"] = {
                "id": str(message.reply_to.id),
                "sender": {
                    "id": str(message.reply_to.sender.id),
                    "username": message.reply_to.sender.username,
                    "first_name": message.reply_to.sender.first_name,
                    "last_name": message.reply_to.sender.last_name,
                    "avatar": None,  # Simplified for WS
                },
                "text_content": message.reply_to.text_content,
                "media_url": message.reply_to.media_url,
                "message_type": message.reply_to.message_type,
            }

        all_participant_ids = list(
            ThreadParticipant.objects.filter(thread=thread, left_at__isnull=True)
            .values_list("user_id", flat=True)
        )

        for p_id in all_participant_ids:
            # Invalidate inbox cache for this participant
            invalidate_inbox_cache(p_id)

            # 1) Deliver the new message to the open chat window
            async_to_sync(channel_layer.group_send)(
                f"user_{str(p_id)}",   # str() prevents UUID serialization error
                {
                    "type": "chat_message",
                    "message": ws_payload,
                }
            )

            # 2) Signal the consumer to re-fetch and push the updated thread
            #    list so the inbox re-orders instantly (thread bubbles to top).
            async_to_sync(channel_layer.group_send)(
                f"user_{str(p_id)}",
                {
                    "type": "thread_list_update",
                }
            )
    except Exception:
        logger.exception("Failed to broadcast WebSocket message")

    return message


def mark_messages_read(user, thread_id):
    """Mark all unread messages in a thread as read for this user."""
    get_thread_for_participant(user, thread_id)  # verify membership
    Message.objects.filter(
        thread_id=thread_id,
        is_read=False,
        is_deleted_for_everyone=False,
    ).exclude(sender=user).update(is_read=True)


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
    message.save(update_fields=["is_deleted_for_everyone", "text_content", "media_url", "updated_at"])
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

    count = messages.update(
        is_deleted_for_everyone=True,
        text_content=None,
        media_url=None,
        updated_at=timezone.now(),
    )
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


def clear_thread_history(user, thread_id):
    """Per-user clear history — sets cleared_history_at to now."""
    _, membership = get_thread_for_participant(user, thread_id)
    membership.cleared_history_at = timezone.now()
    membership.save(update_fields=["cleared_history_at"])

    # Invalidate cache so the next inbox fetch correctly calculates last_message as None
    invalidate_inbox_cache(user.id)

    # Broadcast thread list update via WebSocket so the frontend instantly refreshes
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"user_{str(user.id)}",
            {
                "type": "thread_list_update",
            }
        )
    except Exception:
        logger.exception("Failed to broadcast WebSocket thread_list_update on clear history")


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
