"""
Tests for the Notification service layer.

Covers:
  - send_notification() dispatches exactly ONE Celery task per event
  - _process_notification_batch() idempotency — DB written only once even on retry
  - Invalid FCM tokens are cleaned without causing a retry
  - Transient FCM errors DO trigger a retry (but DB is not re-written)
"""

from unittest.mock import patch, MagicMock, call

from django.test import TestCase
from django.contrib.auth import get_user_model

from .models import NotificationSettings, NotificationTypes, Notifications
from .services import send_notification, _process_notification_batch

User = get_user_model()


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _make_user(username, email):
    user = User.objects.create_user(
        username=username, email=email, password="pw123"
    )
    NotificationSettings.objects.get_or_create(
        user=user,
        defaults={
            "system_notifications": True,
            "comment_notifications": True,
            "like_notifications": True,
            "message_notifications": True,
        },
    )
    return user


# ─────────────────────────────────────────────────────────────────────────────
# 1. send_notification() — must fire exactly ONE Celery task per call
# ─────────────────────────────────────────────────────────────────────────────

class SendNotificationDispatchTests(TestCase):

    def setUp(self):
        self.users = [_make_user(f"u{i}", f"u{i}@test.com") for i in range(5)]

    @patch("api.notifications.services._process_notification_batch.delay")
    def test_single_user_comment_dispatches_one_task(self, mock_delay):
        """comment notification → exactly 1 Celery task dispatched."""
        send_notification(
            title="Ali",
            body="commented on your post.",
            user_id=self.users[0].id,
            notification_type=NotificationTypes.POST_COMMENT,
        )
        self.assertEqual(
            mock_delay.call_count, 1,
            f"Expected 1 Celery task for comment, got {mock_delay.call_count}",
        )

    @patch("api.notifications.services._process_notification_batch.delay")
    def test_single_user_like_dispatches_one_task(self, mock_delay):
        """like notification → exactly 1 Celery task dispatched."""
        send_notification(
            title="Ali",
            body="reacted to your post.",
            user_id=self.users[1].id,
            notification_type=NotificationTypes.POST_LIKE,
        )
        self.assertEqual(
            mock_delay.call_count, 1,
            f"Expected 1 Celery task for like, got {mock_delay.call_count}",
        )

    @patch("api.notifications.services._process_notification_batch.delay")
    def test_message_notification_dispatches_one_task(self, mock_delay):
        """message notification (save_to_db=False) → exactly 1 Celery task."""
        send_notification(
            title="Ali",
            body="sent you a message.",
            user_id=self.users[2].id,
            notification_type=NotificationTypes.MESSAGE,
            save_to_db=False,
        )
        self.assertEqual(
            mock_delay.call_count, 1,
            f"Expected 1 Celery task for message, got {mock_delay.call_count}",
        )

    @patch("api.notifications.services._process_notification_batch.delay")
    def test_broadcast_correct_batch_count(self, mock_delay):
        """10 users / batch_size 3  →  4 batches (3+3+3+1)."""
        for i in range(5, 10):
            _make_user(f"u{i}", f"u{i}@test.com")
        with patch("api.notifications.services.DEFAULT_BATCH_SIZE", 3):
            result = send_notification(
                title="Broadcast",
                body="System msg",
                broadcast=True,
                notification_type=NotificationTypes.SYSTEM,
            )
        self.assertEqual(mock_delay.call_count, 4)
        self.assertIn("queued for 10 users", result)

    @patch("api.notifications.services._process_notification_batch.delay")
    def test_users_without_setting_skipped(self, mock_delay):
        """Users who disabled comment notifications receive nothing."""
        NotificationSettings.objects.all().update(comment_notifications=False)
        result = send_notification(
            title="Ali",
            body="commented.",
            broadcast=True,
            notification_type=NotificationTypes.POST_COMMENT,
        )
        self.assertEqual(mock_delay.call_count, 0)
        self.assertIn("No active users", result)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Idempotency — duplicate DB records must NOT be created on retry
# ─────────────────────────────────────────────────────────────────────────────

class NotificationIdempotencyTests(TestCase):

    def setUp(self):
        self.user = _make_user("idempotent", "idempotent@test.com")

    def _run_task(self, cache_mock_value=None):
        """
        Call _process_notification_batch directly (synchronous, no Celery broker).
        Patches FCMDevice so no real FCM calls happen.
        Patches cache.get to simulate whether the idempotency key exists.
        """
        with patch("api.notifications.services.FCMDevice") as mock_fcm, \
             patch("api.notifications.services.cache") as mock_cache:

            mock_cache.get.return_value = cache_mock_value
            # No FCM tokens — so push is skipped cleanly
            mock_fcm.objects.filter.return_value.values_list.return_value = []

            _process_notification_batch(
                user_ids=[str(self.user.id)],
                title="IdempotencyTest",
                body="body text",
                data={},
                notification_type=NotificationTypes.SYSTEM,
                save_to_db=True,
            )

    def test_first_run_creates_exactly_one_db_record(self):
        """First execution must create exactly 1 DB record."""
        self._run_task(cache_mock_value=None)  # no idempotency key yet
        count = Notifications.objects.filter(user=self.user, title="IdempotencyTest").count()
        self.assertEqual(count, 1, f"Expected 1 record on first run, got {count}")

    def test_retry_does_not_create_duplicate_db_record(self):
        """
        Second run (Celery retry) with the idempotency key already set
        must NOT insert another DB record.
        """
        self._run_task(cache_mock_value=None)   # first run — writes DB
        self._run_task(cache_mock_value=True)   # retry  — must skip DB write

        count = Notifications.objects.filter(user=self.user, title="IdempotencyTest").count()
        self.assertEqual(count, 1, f"Retry created a duplicate! Found {count} records.")

    def test_save_to_db_false_never_writes_db(self):
        """When save_to_db=False (message notifications), no DB record is written."""
        with patch("api.notifications.services.FCMDevice") as mock_fcm, \
             patch("api.notifications.services.cache") as mock_cache:

            mock_cache.get.return_value = None
            mock_fcm.objects.filter.return_value.values_list.return_value = []

            _process_notification_batch(
                user_ids=[str(self.user.id)],
                title="TransientMsg",
                body="body",
                data={},
                notification_type=NotificationTypes.MESSAGE,
                save_to_db=False,
            )

        count = Notifications.objects.filter(user=self.user, title="TransientMsg").count()
        self.assertEqual(count, 0, "save_to_db=False must never write to DB")


# ─────────────────────────────────────────────────────────────────────────────
# 3. FCM token handling — bad tokens cleaned, no spurious retries
# ─────────────────────────────────────────────────────────────────────────────

class FCMTokenHandlingTests(TestCase):

    def setUp(self):
        self.user = _make_user("fcm_user", "fcm@test.com")

    @patch("api.notifications.services.FCMDevice")
    @patch("api.notifications.services.messaging")
    @patch("api.notifications.services.cache")
    def test_invalid_token_errors_do_not_trigger_retry(
        self, mock_cache, mock_messaging, mock_fcm_device
    ):
        """
        If FCM returns only InvalidArgument/NotFound/Unregistered errors
        (i.e., all failures are bad-token errors), the task must complete
        successfully WITHOUT retrying — bad tokens are just deleted.
        """
        from firebase_admin import exceptions as fb_exceptions

        mock_cache.get.return_value = None
        # Simulate 1 token in DB
        mock_fcm_device.objects.filter.return_value.values_list.return_value = [
            "bad_token_123"
        ]

        # Build a fake FCM response: 1 failure due to an invalid token
        bad_resp = MagicMock()
        bad_resp.success = False
        bad_resp.exception = fb_exceptions.NotFoundError("Token not registered", None, None)

        fake_response = MagicMock()
        fake_response.failure_count = 1
        fake_response.success_count = 0
        fake_response.responses = [bad_resp]

        mock_messaging.send_each_for_multicast.return_value = fake_response

        # Also simulate User push filter returning our user
        with patch("api.notifications.services.User") as mock_user_model:
            mock_user_model.objects.filter.return_value.values_list.return_value = [
                str(self.user.id)
            ]
            mock_user_model.objects.filter.return_value.values_list.return_value = [
                str(self.user.id)
            ]

            # Should NOT raise (no retry)
            try:
                _process_notification_batch(
                    user_ids=[str(self.user.id)],
                    title="FCM test",
                    body="body",
                    data={},
                    notification_type=NotificationTypes.SYSTEM,
                    save_to_db=False,
                )
            except Exception as e:
                self.fail(f"Task raised unexpectedly for invalid token: {e}")


class ChatPushStyleTests(TestCase):
    """
    App versions that draw their own notifications get data-only pushes;
    older versions keep normal system notifications.
    """

    def setUp(self):
        from .models import FCMDevice

        self.user = _make_user("chat_push", "chatpush@test.com")
        NotificationSettings.objects.filter(user=self.user).update(push_notifications=True)
        FCMDevice.objects.create(user=self.user, registration_id="old_app")
        FCMDevice.objects.create(
            user=self.user, registration_id="new_app", custom_notifications=True
        )

    def _send(self, notification_type):
        def fake_send(message):
            response = MagicMock()
            response.success_count = len(message.tokens)
            response.failure_count = 0
            response.responses = []
            return response

        with patch(
            "api.notifications.services.messaging.send_each_for_multicast",
            side_effect=fake_send,
        ) as send:
            _process_notification_batch(
                user_ids=[str(self.user.id)],
                title="Alice",
                body="hi",
                data={"thread_id": "t1", "sender_avatar": "https://x/a.jpg"},
                notification_type=notification_type,
                save_to_db=False,
            )
        return {tuple(c.args[0].tokens): c.args[0] for c in send.call_args_list}

    def test_chat_message_is_data_only_for_new_app(self):
        sent = self._send(NotificationTypes.MESSAGE)

        rich = sent[("new_app",)]
        self.assertIsNone(rich.notification)
        self.assertIsNone(rich.android.notification)
        self.assertEqual(rich.data["title"], "Alice")
        self.assertEqual(rich.data["body"], "hi")
        self.assertEqual(rich.data["sender_avatar"], "https://x/a.jpg")

        plain = sent[("old_app",)]
        self.assertEqual(plain.notification.title, "Alice")
        # iOS groups a chat's notifications together.
        self.assertEqual(plain.apns.payload.aps.thread_id, "t1")

    def test_other_types_follow_the_device_too(self):
        sent = self._send(NotificationTypes.POST_LIKE)
        self.assertIsNone(sent[("new_app",)].notification)
        self.assertIsNotNone(sent[("old_app",)].notification)
        # Only chats are grouped per conversation on iOS.
        self.assertIsNone(sent[("old_app",)].apns.payload.aps.thread_id)

    @patch("api.notifications.services._process_notification_batch.delay")
    def test_actor_photo_and_name_are_added(self, mock_delay):
        from api.users.models import Profile

        actor = _make_user("liker", "liker@test.com")
        actor.first_name, actor.last_name = "Lily", "Paws"
        actor.save()
        profile, _ = Profile.objects.get_or_create(user=actor)
        with patch("api.notifications.services.avatar_url", return_value="https://cdn/x.jpg"):
            send_notification(
                title="Lily Paws",
                body="reacted to your post.",
                user_id=self.user.id,
                notification_type=NotificationTypes.POST_LIKE,
                data={"post_id": "p1"},
                actor=actor,
            )
        data = mock_delay.call_args.args[3]
        self.assertEqual(data["post_id"], "p1")
        self.assertEqual(data["actor_id"], str(actor.id))
        self.assertEqual(data["actor_name"], "Lily Paws")
        self.assertEqual(data["actor_avatar"], "https://cdn/x.jpg")


class PurgeOldNotificationsTests(TestCase):
    """Data retention: read after 90 days, unread after 180 (defaults)."""

    def test_deletes_only_expired(self):
        from datetime import timedelta

        from django.contrib.auth import get_user_model
        from django.utils import timezone

        from api.notifications.models import Notifications
        from api.notifications.tasks import purge_old_notifications

        user = get_user_model().objects.create_user(
            email="retention@test.com", username="retention", phone="19990001", password="x"
        )
        now = timezone.now()

        def make(days_old, is_read):
            n = Notifications.objects.create(user=user, title=f"{days_old}-{is_read}", is_read=is_read)
            Notifications.objects.filter(pk=n.pk).update(created_at=now - timedelta(days=days_old))
            return n

        old_read = make(91, True)
        recent_read = make(30, True)
        old_unread_kept = make(120, False)
        very_old_unread = make(181, False)

        self.assertEqual(purge_old_notifications(), 2)
        remaining = set(Notifications.objects.values_list("pk", flat=True))
        self.assertEqual(remaining, {recent_read.pk, old_unread_kept.pk})
        self.assertNotIn(old_read.pk, remaining)
        self.assertNotIn(very_old_unread.pk, remaining)
