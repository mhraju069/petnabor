from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from django.utils import timezone
from datetime import timedelta
from django.core.files.uploadedfile import SimpleUploadedFile

from api.friends.models import Friendship
from .models import Story, StoryReaction, StoryView, StoryReply

User = get_user_model()


class StoryBaseTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.alice = User.objects.create_user(
            email="alice@test.com", username="alice",
            phone="10000001", password="pass",
            is_verified=True,
        )
        self.bob = User.objects.create_user(
            email="bob@test.com", username="bob",
            phone="10000002", password="pass",
            is_verified=True,
        )
        self.charlie = User.objects.create_user(
            email="charlie@test.com", username="charlie",
            phone="10000003", password="pass",
            is_verified=True,
        )

    def make_friends(self, user_a, user_b):
        Friendship.objects.get_or_create(sender=user_a, receiver=user_b)
        Friendship.objects.get_or_create(sender=user_b, receiver=user_a)

    def get_data(self, response):
        return response.data.get('results', response.data) if isinstance(response.data, dict) else response.data


class StoryPublishTestCase(StoryBaseTestCase):
    def test_publish_text_story(self):
        self.client.force_authenticate(self.alice)
        url = reverse('story-list')
        data = {
            "media_type": "TEXT",
            "text_content": "Hello Story",
            "bg_color": "#000000",
            "privacy": "PUBLIC"
        }
        response = self.client.post(url, data, format='json')
        if response.status_code != 201:
            print("ERROR RESP:", response.content)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Story.objects.count(), 1)
        story = Story.objects.first()
        self.assertEqual(story.media_type, "TEXT")
        self.assertEqual(story.text_content, "Hello Story")

    def test_publish_image_story(self):
        self.client.force_authenticate(self.alice)
        url = reverse('story-list')
        image = SimpleUploadedFile("test.jpg", b"file_content", content_type="image/jpeg")
        data = {
            "media_type": "IMAGE",
            "media": image,
            "privacy": "FRIENDS_ONLY"
        }
        response = self.client.post(url, data, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Story.objects.count(), 1)
        story = Story.objects.first()
        self.assertEqual(story.media_type, "IMAGE")

    def test_delete_story(self):
        self.client.force_authenticate(self.alice)
        story = Story.objects.create(author=self.alice, text_content="Delete me", expires_at=timezone.now() + timedelta(days=1))
        url = reverse('story-detail', kwargs={'pk': story.id})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Story.objects.count(), 0)

    def test_delete_other_user_story(self):
        self.client.force_authenticate(self.bob)
        story = Story.objects.create(author=self.alice, text_content="Delete me", expires_at=timezone.now() + timedelta(days=1))
        url = reverse('story-detail', kwargs={'pk': story.id})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class StoryFeedAndListTestCase(StoryBaseTestCase):
    def test_list_own_stories(self):
        Story.objects.create(author=self.alice, text_content="Alice Story 1", expires_at=timezone.now() + timedelta(days=1))
        Story.objects.create(author=self.bob, text_content="Bob Story 1", expires_at=timezone.now() + timedelta(days=1))
        
        self.client.force_authenticate(self.alice)
        url = reverse('story-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        data = self.get_data(response)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['text_content'], "Alice Story 1")

    def test_story_feed(self):
        """Feed returns grouped results: one entry per user, stories nested inside."""
        Story.objects.create(author=self.alice, text_content="Alice Public", privacy="PUBLIC", expires_at=timezone.now() + timedelta(days=1))
        Story.objects.create(author=self.alice, text_content="Alice Friends Only", privacy="FRIENDS_ONLY", expires_at=timezone.now() + timedelta(days=1))
        Story.objects.create(author=self.bob, text_content="Bob Public", privacy="PUBLIC", expires_at=timezone.now() + timedelta(days=1))

        self.client.force_authenticate(self.charlie)
        url = reverse('story-feed')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Charlie has no friends yet — only PUBLIC stories show (alice + bob each 1 group)
        # alice has PUBLIC + FRIENDS_ONLY, but charlie is not friend, so only PUBLIC
        data = self.get_data(response)
        # bob public: 1 group, alice public: 1 group → 2 groups
        self.assertEqual(len(data), 2)

        self.make_friends(self.alice, self.charlie)
        response = self.client.get(url)
        data = self.get_data(response)
        # Alice is now friend: both alice stories + bob public → still 2 groups
        self.assertEqual(len(data), 2)
        # Find alice's group
        alice_group = next(g for g in data if g['user']['username'] == 'alice')
        # Alice has 2 stories (PUBLIC + FRIENDS_ONLY) as friends
        self.assertEqual(len(alice_group['stories']), 2)
        self.assertIn('has_unseen', alice_group)
        self.assertIn('latest_story_at', alice_group)

    def test_feed_groups_same_user_stories(self):
        """Multiple stories from the same user must appear as a single group."""
        Story.objects.create(author=self.alice, text_content="Story 1", privacy="PUBLIC", expires_at=timezone.now() + timedelta(days=1))
        Story.objects.create(author=self.alice, text_content="Story 2", privacy="PUBLIC", expires_at=timezone.now() + timedelta(days=1))
        Story.objects.create(author=self.alice, text_content="Story 3", privacy="PUBLIC", expires_at=timezone.now() + timedelta(days=1))

        self.client.force_authenticate(self.bob)
        url = reverse('story-feed')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = self.get_data(response)
        # All 3 alice stories → exactly 1 group
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['user']['username'], 'alice')
        self.assertEqual(len(data[0]['stories']), 3)

    def test_user_stories(self):
        Story.objects.create(author=self.alice, text_content="Alice Public", privacy="PUBLIC", expires_at=timezone.now() + timedelta(days=1))
        Story.objects.create(author=self.alice, text_content="Alice Friends Only", privacy="FRIENDS_ONLY", expires_at=timezone.now() + timedelta(days=1))
        
        self.client.force_authenticate(self.bob)
        url = reverse('story-user-stories')
        response = self.client.get(url, {'user_id': self.alice.id})
        
        data = self.get_data(response)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['privacy'], "PUBLIC")

        self.make_friends(self.alice, self.bob)
        response = self.client.get(url, {'user_id': self.alice.id})
        data = self.get_data(response)
        self.assertEqual(len(data), 2)


class StoryInteractionTestCase(StoryBaseTestCase):
    def setUp(self):
        super().setUp()
        self.story = Story.objects.create(author=self.alice, text_content="Interaction Story", expires_at=timezone.now() + timedelta(days=1))

    def test_view_story(self):
        self.client.force_authenticate(self.bob)
        url = reverse('story-view', kwargs={'pk': self.story.id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'viewed')
        self.assertEqual(response.data['views_count'], 1)

        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'already_viewed')
        self.assertEqual(response.data['views_count'], 1)

    def test_author_view_own_story(self):
        self.client.force_authenticate(self.alice)
        url = reverse('story-view', kwargs={'pk': self.story.id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(StoryView.objects.count(), 0)

    def test_get_viewers(self):
        self.client.force_authenticate(self.bob)
        self.client.post(reverse('story-view', kwargs={'pk': self.story.id}))
        
        self.client.force_authenticate(self.charlie)
        response = self.client.get(reverse('story-viewers', kwargs={'pk': self.story.id}))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.alice)
        response = self.client.get(reverse('story-viewers', kwargs={'pk': self.story.id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = self.get_data(response)
        self.assertEqual(len(data), 1)

    def test_react_story(self):
        self.client.force_authenticate(self.bob)
        url = reverse('story-react', kwargs={'pk': self.story.id})
        response = self.client.post(url, {"reaction_type": "LIKE"}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(StoryReaction.objects.count(), 1)

        response = self.client.post(url, {"reaction_type": "LOVE"}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(StoryReaction.objects.count(), 1)
        self.assertEqual(StoryReaction.objects.first().reaction_type, "LOVE")

        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(StoryReaction.objects.count(), 0)

    def test_reply_story(self):
        self.client.force_authenticate(self.bob)
        url = reverse('story-reply', kwargs={'pk': self.story.id})
        response = self.client.post(url, {"reply_text": "Nice story!"}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = self.get_data(response)
        self.assertEqual(len(data), 1)

        self.client.force_authenticate(self.alice)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = self.get_data(response)
        self.assertEqual(len(data), 1)


class StoryReplyChatMessageTestCase(StoryBaseTestCase):
    """A story reply lands in the author's chat as a STORY_REPLY message."""

    def setUp(self):
        super().setUp()
        self.story = Story.objects.create(
            author=self.alice,
            text_content="Walk in the park",
            bg_color="#3E77BC",
            expires_at=timezone.now() + timedelta(days=1),
        )

    def _reply(self, user, text="So cute!"):
        self.client.force_authenticate(user)
        return self.client.post(
            reverse("story-reply", kwargs={"pk": self.story.id}),
            {"reply_text": text},
            format="json",
        )

    def test_reply_creates_story_reply_message_in_direct_thread(self):
        from api.messaging.models import Message, MessageTypes, ThreadTypes

        response = self._reply(self.bob)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        message = Message.objects.get()
        self.assertEqual(message.message_type, MessageTypes.STORY_REPLY)
        self.assertEqual(message.sender, self.bob)
        self.assertEqual(message.text_content, "So cute!")
        self.assertEqual(message.metadata["story_id"], str(self.story.id))
        self.assertEqual(message.metadata["story_text"], "Walk in the park")
        self.assertEqual(message.metadata["story_bg_color"], "#3E77BC")

        thread = message.thread
        self.assertEqual(thread.thread_type, ThreadTypes.DIRECT)
        participants = set(thread.participants.values_list("user_id", flat=True))
        self.assertEqual(participants, {self.alice.id, self.bob.id})
        self.assertTrue(thread.last_message_text.startswith("Story reply:"))

    def test_replies_reuse_the_existing_thread(self):
        from api.messaging.models import ChatThread, Message

        self._reply(self.bob, "one")
        self._reply(self.bob, "two")
        self.assertEqual(ChatThread.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 2)

    def test_non_friend_can_reply_to_public_story(self):
        """Seeing a public story is enough to reply, even without friendship."""
        from api.messaging.models import Message

        response = self._reply(self.charlie)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Message.objects.filter(sender=self.charlie).count(), 1)

    def test_blocked_user_reply_does_not_create_message(self):
        from api.friends.models import UserBlock
        from api.messaging.models import Message

        UserBlock.objects.create(blocker=self.alice, blocked_user=self.bob)
        response = self._reply(self.bob)
        # The story itself is hidden from a blocked user.
        self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))
        self.assertEqual(Message.objects.count(), 0)

    def test_replying_to_own_story_sends_no_message(self):
        from api.messaging.models import Message

        response = self._reply(self.alice)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Message.objects.count(), 0)

    def test_clients_cannot_send_story_reply_type_directly(self):
        from api.messaging.models import Message, MessageTypes
        from api.messaging.services import get_or_create_direct_thread

        self.make_friends(self.alice, self.bob)
        thread, _ = get_or_create_direct_thread(self.bob, self.alice)
        self.client.force_authenticate(self.bob)
        response = self.client.post(
            reverse("message-list-create", kwargs={"thread_id": thread.id}),
            {"message_type": MessageTypes.STORY_REPLY, "text_content": "fake"},
            format="json",
        )
        self.assertNotEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Message.objects.count(), 0)
