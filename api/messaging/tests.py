"""
Comprehensive test suite for the messaging system.

Tests are independent of Celery (notifications mocked).
Covers all permission and access-control scenarios.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from api.friends.models import Friendship, UserBlock
from api.users.models import Profile

from .models import ChatThread, Message, MessageTypes, ThreadParticipant, ThreadTypes
from .services import can_message, invalidate_messaging_permission

User = get_user_model()


# ──────────────────────────────────────────────
# Base test setup
# ──────────────────────────────────────────────


class MessagingBaseTestCase(TestCase):
    """Shared setup: four users with profiles. No friendships by default."""

    def setUp(self):
        self.client = APIClient()

        self.alice = User.objects.create_user(
            email="alice@test.com", username="alice",
            phone="10000001", password="pass",
            first_name="Alice", is_verified=True,
        )
        self.bob = User.objects.create_user(
            email="bob@test.com", username="bob",
            phone="10000002", password="pass",
            first_name="Bob", is_verified=True,
        )
        self.carol = User.objects.create_user(
            email="carol@test.com", username="carol",
            phone="10000003", password="pass",
            first_name="Carol", is_verified=True,
        )
        self.dave = User.objects.create_user(
            email="dave@test.com", username="dave",
            phone="10000004", password="pass",
            first_name="Dave", is_verified=True,
        )

        for user in [self.alice, self.bob, self.carol, self.dave]:
            Profile.objects.get_or_create(user=user)

    # ── helpers ──────────────────────────────

    def make_friends(self, user_a, user_b):
        Friendship.objects.get_or_create(sender=user_a, receiver=user_b)
        invalidate_messaging_permission(user_a.id, user_b.id)

    def block(self, blocker, blocked):
        UserBlock.objects.get_or_create(blocker=blocker, blocked_user=blocked)
        invalidate_messaging_permission(blocker.id, blocked.id)


# ──────────────────────────────────────────────
# Permission unit tests (service layer)
# ──────────────────────────────────────────────


class CanMessageServiceTest(MessagingBaseTestCase):

    def test_friends_can_message(self):
        self.make_friends(self.alice, self.bob)
        self.assertTrue(can_message(self.alice, self.bob))

    def test_strangers_cannot_message(self):
        self.assertFalse(can_message(self.alice, self.carol))

    def test_blocked_user_cannot_message(self):
        self.make_friends(self.alice, self.bob)
        self.block(self.alice, self.bob)
        self.assertFalse(can_message(self.alice, self.bob))
        # Bidirectional: bob also cannot message alice
        self.assertFalse(can_message(self.bob, self.alice))

    def test_ex_friend_with_prior_thread_can_still_message(self):
        """Once a thread exists, ex-friends may continue messaging."""
        self.make_friends(self.alice, self.bob)
        # Create a thread while still friends
        thread = ChatThread.objects.create(thread_type=ThreadTypes.DIRECT, created_by=self.alice)
        ThreadParticipant.objects.bulk_create([
            ThreadParticipant(thread=thread, user=self.alice),
            ThreadParticipant(thread=thread, user=self.bob),
        ])
        # Remove friendship
        Friendship.objects.filter(sender=self.alice, receiver=self.bob).delete()
        invalidate_messaging_permission(self.alice.id, self.bob.id)

        # Thread existed → still allowed
        self.assertTrue(can_message(self.alice, self.bob))

    def test_blocked_ex_friend_with_thread_still_denied(self):
        """Block overrides the 'prior thread' exception."""
        self.make_friends(self.alice, self.bob)
        thread = ChatThread.objects.create(thread_type=ThreadTypes.DIRECT, created_by=self.alice)
        ThreadParticipant.objects.bulk_create([
            ThreadParticipant(thread=thread, user=self.alice),
            ThreadParticipant(thread=thread, user=self.bob),
        ])
        Friendship.objects.filter(sender=self.alice, receiver=self.bob).delete()
        self.block(self.alice, self.bob)
        self.assertFalse(can_message(self.alice, self.bob))


# ──────────────────────────────────────────────
# Thread API tests
# ──────────────────────────────────────────────


@patch("api.messaging.tasks.notify_new_message")
class ThreadAPITest(MessagingBaseTestCase):

    def setUp(self):
        super().setUp()
        self.thread_list_url = reverse("thread-list-create")

    # ── Inbox ──

    def test_inbox_returns_only_user_threads(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        self.make_friends(self.carol, self.dave)
        # Alice-Bob thread
        thread_ab = ChatThread.objects.create(thread_type=ThreadTypes.DIRECT, created_by=self.alice)
        ThreadParticipant.objects.bulk_create([
            ThreadParticipant(thread=thread_ab, user=self.alice),
            ThreadParticipant(thread=thread_ab, user=self.bob),
        ])
        # Carol-Dave thread (Alice should NOT see this)
        thread_cd = ChatThread.objects.create(thread_type=ThreadTypes.DIRECT, created_by=self.carol)
        ThreadParticipant.objects.bulk_create([
            ThreadParticipant(thread=thread_cd, user=self.carol),
            ThreadParticipant(thread=thread_cd, user=self.dave),
        ])

        self.client.force_authenticate(self.alice)
        response = self.client.get(self.thread_list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        thread_ids = [t["id"] for t in response.data["results"]]
        self.assertIn(str(thread_ab.id), thread_ids)
        self.assertNotIn(str(thread_cd.id), thread_ids)

    # ── Start DIRECT thread ──

    def test_friend_can_start_direct_thread(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        self.client.force_authenticate(self.alice)
        response = self.client.post(
            self.thread_list_url,
            {"recipient_id": str(self.bob.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ChatThread.objects.filter(thread_type=ThreadTypes.DIRECT).count(), 1)

    def test_second_request_returns_existing_thread(self, mock_notify):
        """Idempotent: starting same DM again returns 200 + existing thread."""
        self.make_friends(self.alice, self.bob)
        self.client.force_authenticate(self.alice)
        url = self.thread_list_url
        data = {"recipient_id": str(self.bob.id)}
        self.client.post(url, data, format="json")
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(ChatThread.objects.filter(thread_type=ThreadTypes.DIRECT).count(), 1)

    def test_stranger_cannot_start_direct_thread(self, mock_notify):
        self.client.force_authenticate(self.alice)
        response = self.client.post(
            self.thread_list_url,
            {"recipient_id": str(self.carol.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_blocked_user_cannot_start_thread(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        self.block(self.alice, self.bob)
        self.client.force_authenticate(self.alice)
        response = self.client.post(
            self.thread_list_url,
            {"recipient_id": str(self.bob.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_nonexistent_recipient_returns_404(self, mock_notify):
        self.client.force_authenticate(self.alice)
        response = self.client.post(
            self.thread_list_url,
            {"recipient_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # ── Group thread ──

    def test_create_group_thread(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        self.client.force_authenticate(self.alice)
        url = reverse("group-thread-create")
        response = self.client.post(
            url,
            {
                "name": "Pet Lovers",
                "member_ids": [str(self.bob.id), str(self.carol.id)],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["thread_type"], ThreadTypes.GROUP)
        thread = ChatThread.objects.get(id=response.data["id"])
        self.assertEqual(thread.participants.filter(left_at__isnull=True).count(), 3)

    def test_create_group_thread_requires_name(self, mock_notify):
        self.client.force_authenticate(self.alice)
        url = reverse("group-thread-create")
        response = self.client.post(url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ──────────────────────────────────────────────
# Message API tests
# ──────────────────────────────────────────────


@patch("api.messaging.tasks.notify_new_message")
class MessageAPITest(MessagingBaseTestCase):

    def _create_direct_thread(self, user_a, user_b):
        thread = ChatThread.objects.create(
            thread_type=ThreadTypes.DIRECT, created_by=user_a
        )
        ThreadParticipant.objects.bulk_create([
            ThreadParticipant(thread=thread, user=user_a),
            ThreadParticipant(thread=thread, user=user_b),
        ])
        return thread

    def _msg_list_url(self, thread_id):
        return reverse("message-list-create", kwargs={"thread_id": thread_id})

    def _msg_detail_url(self, thread_id, message_id):
        return reverse("message-detail", kwargs={"thread_id": thread_id, "message_id": message_id})

    # ── Send message ──

    def test_friend_can_send_message(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        thread = self._create_direct_thread(self.alice, self.bob)
        self.client.force_authenticate(self.alice)
        response = self.client.post(
            self._msg_list_url(thread.id),
            {"text_content": "Hello Bob!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Message.objects.filter(thread=thread).count(), 1)

    def test_notification_sent_on_message(self, mock_notify):
        mock_notify.delay = mock_notify  # simplify mock
        self.make_friends(self.alice, self.bob)
        thread = self._create_direct_thread(self.alice, self.bob)
        self.client.force_authenticate(self.alice)
        self.client.post(
            self._msg_list_url(thread.id),
            {"text_content": "Hey"},
            format="json",
        )
        self.assertTrue(mock_notify.called)

    def test_ex_friend_with_prior_thread_can_send_message(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        thread = self._create_direct_thread(self.alice, self.bob)
        # Remove friendship
        Friendship.objects.filter(sender=self.alice, receiver=self.bob).delete()
        invalidate_messaging_permission(self.alice.id, self.bob.id)

        self.client.force_authenticate(self.alice)
        response = self.client.post(
            self._msg_list_url(thread.id),
            {"text_content": "Still friends?"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_blocked_user_cannot_send_message(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        thread = self._create_direct_thread(self.alice, self.bob)
        self.block(self.alice, self.bob)

        self.client.force_authenticate(self.alice)
        response = self.client.post(
            self._msg_list_url(thread.id),
            {"text_content": "Can I still?"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_participant_cannot_send_message(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        thread = self._create_direct_thread(self.alice, self.bob)

        self.client.force_authenticate(self.carol)  # not in thread
        response = self.client.post(
            self._msg_list_url(thread.id),
            {"text_content": "Crash!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_empty_text_is_rejected(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        thread = self._create_direct_thread(self.alice, self.bob)
        self.client.force_authenticate(self.alice)
        response = self.client.post(
            self._msg_list_url(thread.id),
            {"text_content": "   "},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_image_message_requires_media_url(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        thread = self._create_direct_thread(self.alice, self.bob)
        self.client.force_authenticate(self.alice)
        response = self.client.post(
            self._msg_list_url(thread.id),
            {"message_type": MessageTypes.IMAGE},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_image_message_with_media_url_succeeds(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        thread = self._create_direct_thread(self.alice, self.bob)
        self.client.force_authenticate(self.alice)
        response = self.client.post(
            self._msg_list_url(thread.id),
            {"message_type": MessageTypes.IMAGE, "media_url": "https://example.com/img.png"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    # ── Fetch messages ──

    def test_message_list_paginated(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        thread = self._create_direct_thread(self.alice, self.bob)
        Message.objects.bulk_create([
            Message(thread=thread, sender=self.alice, text_content=f"msg {i}")
            for i in range(35)
        ])
        self.client.force_authenticate(self.alice)
        response = self.client.get(self._msg_list_url(thread.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Cursor pagination returns 30 by default
        self.assertEqual(len(response.data["results"]), 30)

    def test_non_participant_cannot_read_messages(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        thread = self._create_direct_thread(self.alice, self.bob)
        self.client.force_authenticate(self.carol)
        response = self.client.get(self._msg_list_url(thread.id))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # ── Reply threading ──

    def test_reply_to_message(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        thread = self._create_direct_thread(self.alice, self.bob)
        original = Message.objects.create(
            thread=thread, sender=self.bob,
            text_content="Original", message_type=MessageTypes.TEXT,
        )
        self.client.force_authenticate(self.alice)
        response = self.client.post(
            self._msg_list_url(thread.id),
            {"text_content": "Reply!", "reply_to_id": str(original.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        reply = Message.objects.get(id=response.data["id"])
        self.assertEqual(reply.reply_to, original)

    # ── Delete message ──

    def test_sender_can_delete_own_message(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        thread = self._create_direct_thread(self.alice, self.bob)
        message = Message.objects.create(
            thread=thread, sender=self.alice,
            text_content="Delete me", message_type=MessageTypes.TEXT,
        )
        self.client.force_authenticate(self.alice)
        response = self.client.delete(self._msg_detail_url(thread.id, message.id))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        message.refresh_from_db()
        self.assertTrue(message.is_deleted_for_everyone)
        self.assertIsNone(message.text_content)

    def test_non_sender_cannot_delete_message(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        thread = self._create_direct_thread(self.alice, self.bob)
        message = Message.objects.create(
            thread=thread, sender=self.alice,
            text_content="Alice's msg", message_type=MessageTypes.TEXT,
        )
        self.client.force_authenticate(self.bob)
        response = self.client.delete(self._msg_detail_url(thread.id, message.id))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── Clear history ──

    def test_clear_history(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        thread = self._create_direct_thread(self.alice, self.bob)
        self.client.force_authenticate(self.alice)
        response = self.client.post(
            reverse("thread-clear-history", kwargs={"thread_id": thread.id})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership = ThreadParticipant.objects.get(thread=thread, user=self.alice)
        self.assertIsNotNone(membership.cleared_history_at)

    # ── Bulk Delete ──

    def test_bulk_delete_messages(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        thread = self._create_direct_thread(self.alice, self.bob)
        msg1 = Message.objects.create(thread=thread, sender=self.alice, text_content="m1")
        msg2 = Message.objects.create(thread=thread, sender=self.alice, text_content="m2")
        msg3 = Message.objects.create(thread=thread, sender=self.bob, text_content="m3") # not mine

        self.client.force_authenticate(self.alice)
        url = reverse("message-bulk-delete", kwargs={"thread_id": thread.id})
        response = self.client.post(url, {"message_ids": [str(msg1.id), str(msg2.id), str(msg3.id)]}, format="json")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["deleted_count"], 2) # msg3 skipped as not owner
        
        msg1.refresh_from_db()
        msg3.refresh_from_db()
        self.assertTrue(msg1.is_deleted_for_everyone)
        self.assertFalse(msg3.is_deleted_for_everyone)

    # ── Thread Leave/Delete ──

    def test_leave_thread(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        thread = self._create_direct_thread(self.alice, self.bob)
        self.client.force_authenticate(self.alice)
        
        url = reverse("thread-detail", kwargs={"thread_id": thread.id})
        response = self.client.delete(url) # default is leave
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership = ThreadParticipant.objects.get(thread=thread, user=self.alice)
        self.assertIsNotNone(membership.left_at)

    def test_delete_thread_for_everyone_fail_if_not_admin(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        url = reverse("group-thread-create")
        self.client.force_authenticate(self.alice)
        res = self.client.post(url, {"name": "Group", "member_ids": [str(self.bob.id)]}, format="json")
        thread_id = res.data["id"]

        # Bob is member, not admin. Bob tries to delete for everyone.
        self.client.force_authenticate(self.bob)
        detail_url = reverse("thread-detail", kwargs={"thread_id": thread_id})
        response = self.client.delete(f"{detail_url}?everyone=true")
        
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(ChatThread.objects.filter(id=thread_id).exists())

    def test_delete_thread_for_everyone_success_by_admin(self, mock_notify):
        self.make_friends(self.alice, self.bob)
        url = reverse("group-thread-create")
        self.client.force_authenticate(self.alice)
        res = self.client.post(url, {"name": "Group", "member_ids": [str(self.bob.id)]}, format="json")
        thread_id = res.data["id"]

        # Alice is admin. Alice deletes for everyone.
        detail_url = reverse("thread-detail", kwargs={"thread_id": thread_id})
        response = self.client.delete(f"{detail_url}?everyone=true")
        
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ChatThread.objects.filter(id=thread_id).exists())

    # ── Unauthenticated ──

    def test_unauthenticated_request_denied(self, mock_notify):
        response = self.client.get(reverse("thread-list-create"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


@patch("api.messaging.tasks.notify_new_message")
class MessageCursorPaginationTest(MessagingBaseTestCase):
    """GET messages walks back through a long thread via ?cursor= (newest first)."""

    TOTAL = 70

    def setUp(self):
        super().setUp()
        from datetime import timedelta

        from django.utils import timezone

        self.thread = ChatThread.objects.create(
            thread_type=ThreadTypes.DIRECT, created_by=self.alice
        )
        ThreadParticipant.objects.bulk_create([
            ThreadParticipant(thread=self.thread, user=self.alice),
            ThreadParticipant(thread=self.thread, user=self.bob),
        ])
        Message.objects.bulk_create([
            Message(
                thread=self.thread,
                sender=self.alice if i % 2 else self.bob,
                message_type=MessageTypes.TEXT,
                text_content=f"msg {i}",
            )
            for i in range(self.TOTAL)
        ])
        # Distinct, increasing timestamps: msg 0 oldest … msg 69 newest.
        start = timezone.now() - timedelta(hours=2)
        for i in range(self.TOTAL):
            Message.objects.filter(thread=self.thread, text_content=f"msg {i}").update(
                created_at=start + timedelta(seconds=i)
            )
        self.url = reverse("message-list-create", kwargs={"thread_id": self.thread.id})
        self.client.force_authenticate(self.alice)

    def test_pages_cover_every_message_once_newest_first(self, _mock):
        seen = []
        url = self.url
        pages = 0
        while url:
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertIn("next", response.data)
            seen.extend(m["text_content"] for m in response.data["results"])
            url = response.data["next"]
            pages += 1
            self.assertLess(pages, 10, "cursor never ended")

        self.assertEqual(pages, 3)  # 30 + 30 + 10
        self.assertEqual(len(seen), self.TOTAL)
        self.assertEqual(len(set(seen)), self.TOTAL)  # no duplicates
        self.assertEqual(seen[0], f"msg {self.TOTAL - 1}")  # newest first
        self.assertEqual(seen[-1], "msg 0")

    def test_page_size_param(self, _mock):
        response = self.client.get(self.url, {"page_size": 50})
        self.assertEqual(len(response.data["results"]), 50)
        capped = self.client.get(self.url, {"page_size": 1000})
        self.assertEqual(len(capped.data["results"]), self.TOTAL)  # cap is 100


# ──────────────────────────────────────────────
# Photo messages
# ──────────────────────────────────────────────

import shutil
import tempfile
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

_TMP_MEDIA = tempfile.mkdtemp()


def _png(name="photo.png"):
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


@override_settings(
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": _TMP_MEDIA, "base_url": "/media/"},
        },
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
@patch("api.messaging.tasks.notify_new_message")
class PhotoMessageTest(MessagingBaseTestCase):

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.make_friends(self.alice, self.bob)
        from .services import get_or_create_direct_thread

        self.thread, _ = get_or_create_direct_thread(self.alice, self.bob)
        self.url = reverse("message-list-create", kwargs={"thread_id": self.thread.id})
        self.client.force_authenticate(self.alice)

    def test_upload_photo_creates_image_message(self, _mock):
        response = self.client.post(self.url, {"media": _png()}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["message_type"], MessageTypes.IMAGE)
        self.assertTrue(response.data["media_url"])

        message = Message.objects.get()
        self.assertTrue(message.media_file.name.startswith("chat_media/"))
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.last_message_text, "📷 Photo")

    def test_photo_with_caption(self, _mock):
        response = self.client.post(
            self.url, {"media": _png(), "text_content": "Look at him!"}, format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["text_content"], "Look at him!")
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.last_message_text, "📷 Look at him!")

    def test_non_image_file_is_rejected(self, _mock):
        bad = SimpleUploadedFile("notes.png", b"not an image", content_type="image/png")
        response = self.client.post(self.url, {"media": bad}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Message.objects.count(), 0)

    def test_non_participant_cannot_upload(self, _mock):
        self.client.force_authenticate(self.carol)
        response = self.client.post(self.url, {"media": _png()}, format="multipart")
        self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))
        self.assertEqual(Message.objects.count(), 0)

    def test_delete_for_everyone_clears_photo(self, _mock):
        self.client.post(self.url, {"media": _png()}, format="multipart")
        message = Message.objects.get()
        detail = reverse(
            "message-detail", kwargs={"thread_id": self.thread.id, "message_id": message.id}
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(detail)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        message.refresh_from_db()
        self.assertTrue(message.is_deleted_for_everyone)
        self.assertFalse(message.media_file)
        self.assertIsNone(message.media_url)


# ──────────────────────────────────────────────
# Mute / unmute
# ──────────────────────────────────────────────


class ThreadMuteTest(MessagingBaseTestCase):

    def setUp(self):
        super().setUp()
        self.make_friends(self.alice, self.bob)
        from .services import get_or_create_direct_thread

        self.thread, _ = get_or_create_direct_thread(self.alice, self.bob)
        self.mute_url = reverse("thread-mute", kwargs={"thread_id": self.thread.id})
        self.detail_url = reverse("thread-detail", kwargs={"thread_id": self.thread.id})
        self.messages_url = reverse("message-list-create", kwargs={"thread_id": self.thread.id})

    def test_mute_and_unmute(self):
        self.client.force_authenticate(self.bob)

        response = self.client.post(self.mute_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_muted"])
        self.assertTrue(self.client.get(self.detail_url).data["is_muted"])

        response = self.client.delete(self.mute_url)
        self.assertFalse(response.data["is_muted"])
        self.assertFalse(self.client.get(self.detail_url).data["is_muted"])

    def test_mute_is_per_user(self):
        self.client.force_authenticate(self.bob)
        self.client.post(self.mute_url)
        self.client.force_authenticate(self.alice)
        self.assertFalse(self.client.get(self.detail_url).data["is_muted"])

    @patch("api.messaging.tasks.notify_new_message")
    def test_muted_user_gets_no_push(self, notify):
        self.client.force_authenticate(self.bob)
        self.client.post(self.mute_url)

        self.client.force_authenticate(self.alice)
        response = self.client.post(self.messages_url, {"text_content": "hi"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # The message is still delivered, but no push is queued for Bob.
        self.assertEqual(Message.objects.filter(thread=self.thread).count(), 1)
        notify.delay.assert_not_called()

    @patch("api.messaging.tasks.notify_new_message")
    def test_unmuted_user_gets_push(self, notify):
        self.client.force_authenticate(self.alice)
        self.client.post(self.messages_url, {"text_content": "hi"}, format="json")
        notify.delay.assert_called_once()
        self.assertIn(str(self.bob.id), notify.delay.call_args.args[3])

    def test_non_participant_cannot_mute(self):
        self.client.force_authenticate(self.carol)
        response = self.client.post(self.mute_url)
        self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))


# ──────────────────────────────────────────────
# Admin privacy
# ──────────────────────────────────────────────


class MessageAdminPrivacyTest(MessagingBaseTestCase):
    """Staff see message metadata, not content — unless the message is reported."""

    def setUp(self):
        super().setUp()
        from .services import get_or_create_direct_thread

        self.admin_user = User.objects.create_superuser(
            email="admin@test.com", username="admin", phone="10000099", password="pass"
        )
        thread, _ = get_or_create_direct_thread(self.alice, self.bob)
        self.private = Message.objects.create(thread=thread, sender=self.alice, text_content="my secret plans")
        self.reported = Message.objects.create(thread=thread, sender=self.bob, text_content="nasty insult")
        from api.report.models import Report

        Report.objects.create(
            reporter=self.alice, target_type="MESSAGE", target_id=self.reported.id, reason="abuse"
        )
        self.client.force_login(self.admin_user)

    def test_list_hides_private_text_but_shows_reported(self):
        response = self.client.get(reverse("admin:messaging_message_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "my secret plans")
        self.assertContains(response, "nasty insult")

    def test_content_is_not_searchable(self):
        response = self.client.get(reverse("admin:messaging_message_changelist"), {"q": "secret"})
        self.assertNotContains(response, str(self.private.id)[:8])

    def test_detail_hides_private_text_and_is_read_only(self):
        url = reverse("admin:messaging_message_change", args=[self.private.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "my secret plans")
        self.client.post(url, {"text_content": "edited"})
        self.private.refresh_from_db()
        self.assertEqual(self.private.text_content, "my secret plans")
