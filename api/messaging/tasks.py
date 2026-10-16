"""
Celery tasks for the messaging app.

Sending push notifications asynchronously so message delivery
is never blocked by downstream push notification latency.
"""

import logging

from celery import shared_task
from django.contrib.auth import get_user_model

from api.notifications.models import NotificationTypes
from api.notifications.services import avatar_url, send_notification

logger = logging.getLogger(__name__)
User = get_user_model()


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def notify_new_message(self, message_id, thread_id, sender_id, recipient_ids, text_preview):
    """
    Send push notifications to all recipients of a new message.

    Args:
        message_id:    UUID string of the newly created Message.
        thread_id:     UUID string of the ChatThread.
        sender_id:     UUID string of the message sender.
        recipient_ids: List of UUID strings for users to notify.
        text_preview:  First 100 chars of the message for the notification body.
    """
    try:
        sender = User.objects.select_related("profile").filter(id=sender_id).first()
        if not sender:
            logger.warning("notify_new_message: sender %s not found", sender_id)
            return

        sender_display = f"{sender.first_name} {sender.last_name}".strip() or sender.username or sender.email or "Someone"
        title = sender_display
        body = f"{text_preview}"

        from .models import ChatThread, ThreadTypes

        thread = ChatThread.objects.filter(id=thread_id).only("thread_type", "name").first()
        is_group = bool(thread and thread.thread_type == ThreadTypes.GROUP)

        send_notification(
            user_ids=recipient_ids,
            title=title,
            body=body,
            notification_type=NotificationTypes.MESSAGE,
            save_to_db=False,  # Message notifications are transient — FCM only, no DB record
            actor=sender,
            data={
                "message_id": message_id,
                "thread_id": thread_id,
                "sender_id": sender_id,
                "message": text_preview,
                "type": "new_message",
                "sender_name": sender_display,
                "sender_avatar": avatar_url(sender),
                "is_group": "1" if is_group else "",
                "thread_name": (thread.name or "") if is_group else "",
            },
        )
    except Exception as exc:
        logger.exception("notify_new_message task failed for message %s", message_id)
        raise self.retry(exc=exc)
