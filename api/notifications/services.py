"""
Services for sending push and in-app notifications.

Handles batching and asynchronous processing via Celery.
"""
import logging

from celery import shared_task
from django.core.cache import cache
from firebase_admin import messaging, exceptions
from django.contrib.auth import get_user_model
from .models import FCMDevice, Notifications, NotificationTypes

logger = logging.getLogger(__name__)

User = get_user_model()

# Map notification types to their required setting field
NOTIFICATION_SETTINGS_MAP = {
    NotificationTypes.FRIEND_REQUEST: "friend_request_notifications",
    NotificationTypes.FRIEND_ACCEPT: "friend_request_notifications",
    NotificationTypes.POST_LIKE: "like_notifications",
    NotificationTypes.STORY_LIKE: "like_notifications",
    NotificationTypes.POST_COMMENT: "comment_notifications",
    NotificationTypes.STORY_COMMENT: "comment_notifications",
    NotificationTypes.POST_MENTION: "mention_notifications",
    NotificationTypes.STORY_MENTION: "mention_notifications",
    NotificationTypes.MEETUP_INVITE: "meetup_notifications",
    NotificationTypes.MEETUP_UPDATE: "meetup_notifications",
    NotificationTypes.SYSTEM: "system_notifications",
    NotificationTypes.PROMOTION: "marketing_notifications",
    NotificationTypes.MARKETING: "marketing_notifications",
    NotificationTypes.REWARD: "marketing_notifications",
    NotificationTypes.REFERRAL_BONUS: "marketing_notifications",
    NotificationTypes.STREAK_BONUS: "marketing_notifications",
    NotificationTypes.MESSAGE: "message_notifications",
    NotificationTypes.TIP_RECEIVED: "system_notifications",
    NotificationTypes.TIP_SENT: "system_notifications",
    NotificationTypes.TIP_ENABLE_REQUEST: "system_notifications",
}

DEFAULT_BATCH_SIZE = 500


def avatar_url(user) -> str:
    """Absolute URL of a user's profile picture, or "" (no local paths)."""
    profile = getattr(user, "profile", None)
    picture = getattr(profile, "profile_picture", None)
    if not picture:
        return ""
    try:
        url = picture.url
    except Exception:
        return ""
    return url if url.startswith("http") else ""


def actor_data(actor) -> dict:
    """
    Who caused a notification (liked, commented, sent a request…), so the
    app can show their photo: on the phone's notification and in the
    in-app list.
    """
    if actor is None:
        return {}
    name = f"{actor.first_name or ''} {actor.last_name or ''}".strip() or actor.username or "Someone"
    return {
        "actor_id": str(actor.id),
        "actor_name": name,
        "actor_avatar": avatar_url(actor),
    }


def send_notification(
    title,
    body,
    user_id=None,
    user_ids=None,
    broadcast=False,
    data=None,
    notification_type=NotificationTypes.INFO,
    filters=None,
    save_to_db=True,
    actor=None,
):
    """
    Unified entrypoint for triggering notifications.
    Handles single user, multi-user, and broadcast efficiently while pushing heavy work to Celery.

    actor: the user who caused it (optional); adds their id, name and photo
    to the data.
    """
    if actor is not None:
        data = {**actor_data(actor), **(data or {})}

    users_query = User.objects.all()

    if broadcast:
        if filters:
            users_query = users_query.filter(**filters)
    elif user_id:
        users_query = users_query.filter(id=user_id)
    elif user_ids:
        users_query = users_query.filter(id__in=user_ids)
    else:
        return "No target users provided."

    # Pre-filter by user's specific notification setting to minimize database queries in the batch.
    # We use an OR condition: send if the setting is True OR if the user has no settings row yet
    # (which means they are a new user and all settings default to True).
    setting_field = NOTIFICATION_SETTINGS_MAP.get(notification_type)
    if setting_field:
        from django.db.models import Q
        users_query = users_query.filter(
            Q(**{f"notification_settings__{setting_field}": True})
            | Q(notification_settings__isnull=True)
        )

    total_count = users_query.count()
    if total_count == 0:
        return "No active users found with the required settings."

    batch_size = DEFAULT_BATCH_SIZE
    users_query = users_query.order_by("id")

    batch_ids = []
    # Using iterator(chunk_size=batch_size) is more efficient for very large datasets
    for uid in users_query.values_list("id", flat=True).iterator(
        chunk_size=batch_size
    ):
        batch_ids.append(uid)
        if len(batch_ids) >= batch_size:
            _process_notification_batch.delay(
                batch_ids, title, body, data, notification_type, save_to_db
            )
            batch_ids = []

    if batch_ids:
        _process_notification_batch.delay(
            batch_ids, title, body, data, notification_type, save_to_db
        )

    return f"Notification queued for {total_count} users."


def _build_multicast(tokens, title, body, payload_data, notification_type, data_only):
    """
    data_only=True: no notification block, so Android hands the push to the
    app, which shows it itself (with the actor's photo; chats in
    conversation style). Title and body travel in the data instead.
    """
    is_message = notification_type == NotificationTypes.MESSAGE
    # iOS stacks notifications that share a thread-id (one group per chat).
    thread_id = payload_data.get("thread_id") if is_message else None

    if data_only:
        return messaging.MulticastMessage(
            data={**payload_data, "title": title, "body": body},
            tokens=tokens,
            android=messaging.AndroidConfig(priority="high"),
        )

    return messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data=payload_data,
        tokens=tokens,
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(
                priority="high",
                sound="default",
                channel_id="high-priority",
                default_vibrate_timings=True,
                default_sound=True,
            ),
        ),
        apns=messaging.APNSConfig(
            headers={"apns-priority": "10"},
            payload=messaging.APNSPayload(
                aps=messaging.Aps(
                    alert=messaging.ApsAlert(title=title, body=body),
                    badge=1,
                    sound="default",
                    content_available=True,
                    thread_id=thread_id,
                )
            ),
        ),
        webpush=messaging.WebpushConfig(
            headers={"Urgency": "high"},
            notification=messaging.WebpushNotification(
                title=title,
                body=body,
                icon="/static/icons/notification-icon.png",
            ),
        ),
    )


@shared_task(bind=True, max_retries=3, default_retry_delay=5)
def _process_notification_batch(
    self, user_ids, title, body, data=None, notification_type=NotificationTypes.INFO, save_to_db=True, retry_tokens=None
):
    """Celery task to handle the actual creation of DB records and FCM sending."""
    task_id = self.request.id
    db_done_key = f"notif_db_done:{task_id}"
    db_already_written = cache.get(db_done_key)

    try:
        # Filter out user_ids that do not exist in the DB to avoid ForeignKeyViolation
        existing_user_ids = set(
            User.objects.filter(id__in=user_ids).values_list("id", flat=True)
        )

        if save_to_db and not db_already_written and existing_user_ids:
            notifications_to_create = [
                Notifications(
                    user_id=uid,
                    title=title,
                    body=body,
                    data=data or {},
                    notification_type=notification_type,
                )
                for uid in existing_user_ids
            ]
            Notifications.objects.bulk_create(
                notifications_to_create, ignore_conflicts=True
            )
            cache.set(db_done_key, True, timeout=3600)
            logger.info(
                "[notify] DB records created: type=%s count=%d task=%s",
                notification_type, len(notifications_to_create), task_id,
            )

    except Exception as db_exc:
        logger.exception(
            "[notify] DB write failed for task %s, will retry. Error: %s",
            task_id, db_exc,
        )
        raise self.retry(exc=db_exc)

    try:
        if retry_tokens is not None:
            tokens = retry_tokens
        else:
            users_with_push = User.objects.filter(
                id__in=user_ids, notification_settings__push_notifications=True
            )
            push_user_ids = list(users_with_push.values_list("id", flat=True))

            if not push_user_ids:
                return "Batch Processed: DB records created, but no users had push enabled."

            tokens = list(
                FCMDevice.objects.filter(user_id__in=push_user_ids).values_list(
                    "registration_id", flat=True
                )
            )

        if not tokens:
            return "Batch Processed: DB records created, but no FCM tokens found."

        payload_data = {"type": notification_type}
        if data:
            payload_data.update({k: str(v) for k, v in data.items()})

        # App versions that draw their own notifications (the other
        # person's photo; chats stacked per conversation) get data-only
        # pushes. Older app versions keep normal system notifications.
        rich_tokens = set(
            FCMDevice.objects.filter(
                registration_id__in=tokens, custom_notifications=True
            ).values_list("registration_id", flat=True)
        )
        plain = [t for t in tokens if t not in rich_tokens]
        rich = [t for t in tokens if t in rich_tokens]
        groups = [(group, data_only) for group, data_only in ((plain, False), (rich, True)) if group]

        success_count = 0
        failure_count = 0
        invalid_tokens = []
        transient_tokens = []
        fatal_failures = 0

        for group_tokens, data_only in groups:
            fcm_message = _build_multicast(
                group_tokens, title, body, payload_data, notification_type, data_only
            )
            response = messaging.send_each_for_multicast(fcm_message)
            success_count += response.success_count
            failure_count += response.failure_count

            for idx, resp in enumerate(response.responses):
                if resp.success:
                    continue
                err_name = type(resp.exception).__name__
                if err_name in ('InvalidArgumentError', 'NotFoundError', 'UnregisteredError', 'SenderIdMismatchError'):
                    # Bad tokens are removed and not retried
                    invalid_tokens.append(group_tokens[idx])
                    logger.warning(
                        "[notify] Removing invalid FCM token (idx=%d): %s",
                        idx, err_name,
                    )
                elif err_name in ('UnavailableError', 'InternalError', 'TimeoutError'):
                    # Server errors on Google's side — we should retry
                    transient_tokens.append(group_tokens[idx])
                    logger.warning(
                        "[notify] Transient FCM server error (idx=%d): %s",
                        idx, resp.exception,
                    )
                else:
                    # Auth errors, Quota errors, etc. Systemic token issues — delete them as well
                    fatal_failures += 1
                    invalid_tokens.append(group_tokens[idx])
                    logger.error(
                        "[notify] Fatal configuration/auth FCM error (idx=%d): [%s] %s. Token scheduled for deletion.",
                        idx, err_name, resp.exception,
                    )

        if invalid_tokens:
            FCMDevice.objects.filter(registration_id__in=invalid_tokens).delete()
            logger.info(
                "[notify] Deleted %d invalid FCM tokens.", len(invalid_tokens)
            )

        logger.info(
            "[notify] FCM result: sent=%d failed=%d (token_errors=%d transient=%d fatal=%d) task=%s",
            success_count,
            failure_count,
            len(invalid_tokens),
            len(transient_tokens),
            fatal_failures,
            task_id,
        )

        if transient_tokens:
            raise self.retry(
                kwargs={
                    "user_ids": user_ids,
                    "title": title,
                    "body": body,
                    "data": data,
                    "notification_type": notification_type,
                    "save_to_db": save_to_db,
                    "retry_tokens": transient_tokens,
                },
                exc=Exception(
                    f"{len(transient_tokens)} transient FCM server failures; retrying push."
                )
            )

        return (
            f"Batch Processed: {success_count} sent, "
            f"{failure_count} failed "
            f"({len(invalid_tokens)} bad tokens removed, {fatal_failures} fatal errors)."
        )

    except self.MaxRetriesExceededError:
        logger.error(
            "[notify] Max retries exceeded for task %s. Giving up on FCM push.", task_id
        )
        return "Max retries exceeded. In-app notifications were saved; FCM push gave up."

    except Exception as fcm_exc:
        logger.exception(
            "[notify] Unexpected FCM error for task %s, will retry. Error: %s",
            task_id, fcm_exc,
        )
        raise self.retry(exc=fcm_exc)
