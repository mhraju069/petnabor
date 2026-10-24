from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from django.core.files.uploadedfile import SimpleUploadedFile

from api.friends.models import Friendship, UserBlock
from .models import Post, PostComment, PostMedia, PrivacyChoices

User = get_user_model()


def make_test_image(name="test.jpg"):
    """A real (tiny) JPEG; storage backends may reject non-image bytes."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color=(200, 120, 40)).save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


class PostBaseTestCase(TestCase):
    """Base setup for post tests."""

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
        # Profile creation handled by signal

    def make_friends(self, user_a, user_b):
        Friendship.objects.get_or_create(sender=user_a, receiver=user_b)


class PostAPITest(PostBaseTestCase):
    """Tests for Post CRUD and Feed."""

    def test_create_post_text_only(self):
        self.client.force_authenticate(self.alice)
        url = reverse('post-list')
        data = {
            "content_text": "Hello world #test",
            "privacy": "PUBLIC"
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Post.objects.count(), 1)
        self.assertEqual(Post.objects.first().hashtags.count(), 1)

    def test_create_post_with_media(self):
        self.client.force_authenticate(self.alice)
        url = reverse('post-list')
        image = make_test_image()
        data = {
            "content_text": "Post with media",
            "media": [image]
        }
        # Use format='multipart' for file uploads
        response = self.client.post(url, data, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(PostMedia.objects.count(), 1)

    def test_feed_visibility_friends_only(self):
        # The feed is a public discovery feed: everyone sees PUBLIC posts,
        # FRIENDS_ONLY posts only reach friends.
        Post.objects.create(author=self.alice, content_text="Public", privacy=PrivacyChoices.PUBLIC)
        Post.objects.create(author=self.alice, content_text="Friends", privacy=PrivacyChoices.FRIENDS_ONLY)

        # Bob (stranger) sees only the public post
        self.client.force_authenticate(self.bob)
        response = self.client.get(reverse('post-feed'))
        self.assertEqual([p['content_text'] for p in response.data['results']], ["Public"])

        # Make them friends -> now he sees both
        self.make_friends(self.alice, self.bob)
        response = self.client.get(reverse('post-feed'))
        self.assertEqual(len(response.data['results']), 2)

    def test_guest_can_see_public_post_via_list(self):
        # Alice creates a public post
        Post.objects.create(author=self.alice, content_text="Public", privacy=PrivacyChoices.PUBLIC)
        
        # Bob (stranger) checks Alice's posts via PostService logic (list view)
        self.client.force_authenticate(self.bob)
        # We need a detail or list view that uses get_user_posts
        url = reverse('post-list') # Standard list returns own posts
        # To see others, we usually have a profile posts endpoint.
        # Let's check permissions on individual post
        post = Post.objects.first()
        url = reverse('post-detail', kwargs={'pk': post.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_feed_privacy_friends_only(self):
        # Alice creates a friends-only post
        Post.objects.create(author=self.alice, content_text="Friends Only", privacy=PrivacyChoices.FRIENDS_ONLY)
        
        # Bob (stranger) should NOT see it even if he follows (he needs to be a friend)
        self.client.force_authenticate(self.bob)
        response = self.client.get(reverse('post-feed'))
        self.assertEqual(len(response.data['results']), 0)
        
        # Make them friends
        self.make_friends(self.alice, self.bob)
        response = self.client.get(reverse('post-feed'))
        self.assertEqual(len(response.data['results']), 1)

    def test_soft_delete(self):
        post = Post.objects.create(author=self.alice, content_text="Delete me")
        self.client.force_authenticate(self.alice)
        url = reverse('post-detail', kwargs={'pk': post.id})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        post.refresh_from_db()
        self.assertTrue(post.is_deleted)

    def test_user_post_list_uses_target_user_from_url(self):
        alice_public = Post.objects.create(
            author=self.alice,
            content_text="Alice public post",
            privacy=PrivacyChoices.PUBLIC,
        )
        Post.objects.create(
            author=self.bob,
            content_text="Bob public post",
            privacy=PrivacyChoices.PUBLIC,
        )

        self.client.force_authenticate(self.bob)
        url = reverse('user-post-list', kwargs={'user_id': self.alice.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['id'], str(alice_public.id))
        self.assertEqual(response.data['results'][0]['author']['id'], str(self.alice.id))

    def test_user_post_list_returns_requested_users_posts_not_requesters_posts(self):
        alice_post = Post.objects.create(
            author=self.alice,
            content_text="Alice only post",
            privacy=PrivacyChoices.PUBLIC,
        )
        bob_post = Post.objects.create(
            author=self.bob,
            content_text="Bob only post",
            privacy=PrivacyChoices.PUBLIC,
        )

        self.client.force_authenticate(self.alice)
        url = reverse('user-post-list', kwargs={'user_id': self.bob.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {item['id'] for item in response.data['results']}
        self.assertIn(str(bob_post.id), returned_ids)
        self.assertNotIn(str(alice_post.id), returned_ids)

    def test_user_post_list_non_friend_sees_only_public_posts(self):
        public_post = Post.objects.create(
            author=self.alice,
            content_text="Alice public",
            privacy=PrivacyChoices.PUBLIC,
        )
        friends_only_post = Post.objects.create(
            author=self.alice,
            content_text="Alice friends only",
            privacy=PrivacyChoices.FRIENDS_ONLY,
        )

        self.client.force_authenticate(self.bob)
        response = self.client.get(reverse('user-post-list', kwargs={'user_id': self.alice.id}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {item['id'] for item in response.data['results']}
        self.assertIn(str(public_post.id), returned_ids)
        self.assertNotIn(str(friends_only_post.id), returned_ids)

    def test_user_post_list_friend_sees_public_and_friends_only_posts(self):
        public_post = Post.objects.create(
            author=self.alice,
            content_text="Alice public",
            privacy=PrivacyChoices.PUBLIC,
        )
        friends_only_post = Post.objects.create(
            author=self.alice,
            content_text="Alice friends only",
            privacy=PrivacyChoices.FRIENDS_ONLY,
        )
        private_post = Post.objects.create(
            author=self.alice,
            content_text="Alice private",
            privacy=PrivacyChoices.PRIVATE,
        )

        self.make_friends(self.alice, self.bob)
        self.client.force_authenticate(self.bob)
        response = self.client.get(reverse('user-post-list', kwargs={'user_id': self.alice.id}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {item['id'] for item in response.data['results']}
        self.assertIn(str(public_post.id), returned_ids)
        self.assertIn(str(friends_only_post.id), returned_ids)
        self.assertNotIn(str(private_post.id), returned_ids)

    def test_user_post_list_blocked_user_sees_no_posts(self):
        Post.objects.create(
            author=self.alice,
            content_text="Alice public",
            privacy=PrivacyChoices.PUBLIC,
        )
        UserBlock.objects.create(blocker=self.alice, blocked_user=self.bob)

        self.client.force_authenticate(self.bob)
        response = self.client.get(reverse('user-post-list', kwargs={'user_id': self.alice.id}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['results'], [])

    def test_user_post_list_blocker_also_sees_no_posts(self):
        Post.objects.create(
            author=self.alice,
            content_text="Alice public",
            privacy=PrivacyChoices.PUBLIC,
        )
        UserBlock.objects.create(blocker=self.bob, blocked_user=self.alice)

        self.client.force_authenticate(self.bob)
        response = self.client.get(
            reverse('user-post-list', kwargs={'user_id': self.alice.id})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['results'], [])


class InteractionAPITest(PostBaseTestCase):
    """Tests for Likes, Comments, and Saves."""

    def setUp(self):
        super().setUp()
        self.post = Post.objects.create(author=self.alice, content_text="Interaction Test")

    def test_any_user_can_like(self):
        # Any signed-in user who can see the post may like it, not just the author.
        url = reverse('post-like', kwargs={'pk': self.post.id})

        self.client.force_authenticate(self.bob)
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.client.force_authenticate(self.alice)
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.post.refresh_from_db()
        self.assertEqual(self.post.likes_count, 2)

    def test_create_comment_and_reply(self):
        self.client.force_authenticate(self.bob)
        url = reverse('comment-list')
        
        # Comment
        data = {"post": str(self.post.id), "comment_text": "Nice post!"}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        comment_id = response.data['id']
        
        # Reply
        data = {
            "post": str(self.post.id),
            "parent_comment": comment_id,
            "comment_text": "I agree!"
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        self.post.refresh_from_db()
        self.assertEqual(self.post.comments_count, 2)
        
        parent = PostComment.objects.get(id=comment_id)
        self.assertEqual(parent.replies_count, 1)

    def test_any_user_can_save(self):
        # Any signed-in user who can see the post may save it, not just the author.
        url = reverse('post-save-post', kwargs={'pk': self.post.id})

        self.client.force_authenticate(self.bob)
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.client.force_authenticate(self.alice)
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class PostDeleteRemovesMediaTest(PostBaseTestCase):
    """Deleting a post removes its media files from storage (Cloudinary)."""

    def _deleted_file_names(self, post):
        from unittest import mock
        from django.db.models.fields.files import FieldFile

        deleted = []

        def fake_delete(field_file, save=True):
            deleted.append(field_file.name)

        self.client.force_authenticate(self.alice)
        with mock.patch.object(FieldFile, "delete", autospec=True, side_effect=fake_delete):
            # django-cleanup deletes files after the transaction commits.
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.delete(reverse("post-detail", kwargs={"pk": post.id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return deleted

    def test_post_media_files_are_deleted(self):
        post = Post.objects.create(author=self.alice, content_text="With photo")
        PostMedia.objects.create(
            post=post,
            media_type="IMAGE",
            file="post_media/a.webp",
            medium_file="post_media/a_medium.webp",
            thumbnail_file="post_media/a_thumb.webp",
        )

        deleted = self._deleted_file_names(post)

        post.refresh_from_db()
        self.assertTrue(post.is_deleted)
        self.assertEqual(PostMedia.objects.filter(post=post).count(), 0)
        self.assertCountEqual(
            deleted,
            ["post_media/a.webp", "post_media/a_medium.webp", "post_media/a_thumb.webp"],
        )

    def test_comment_attachments_are_deleted(self):
        post = Post.objects.create(author=self.alice, content_text="Text post")
        comment = PostComment.objects.create(
            post=post, user=self.bob, comment_text="pic", media_file="comment_media/c.webp"
        )

        deleted = self._deleted_file_names(post)

        comment.refresh_from_db()
        self.assertFalse(comment.media_file)
        self.assertIn("comment_media/c.webp", deleted)

    def test_other_users_cannot_delete(self):
        post = Post.objects.create(author=self.alice, content_text="Mine")
        PostMedia.objects.create(post=post, media_type="IMAGE", file="post_media/b.webp")
        self.client.force_authenticate(self.bob)
        response = self.client.delete(reverse("post-detail", kwargs={"pk": post.id}))
        self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))
        self.assertEqual(PostMedia.objects.filter(post=post).count(), 1)


class FeedNewCountTest(PostBaseTestCase):
    """Explore tab badge: posts by others since the last visit."""

    def test_counts_only_new_posts_by_others(self):
        from datetime import timedelta

        from django.utils import timezone

        url = reverse('post-feed-new-count')
        self.client.force_authenticate(self.alice)

        # First visit: nothing to compare against, but we get a start time.
        first = self.client.get(url)
        self.assertEqual(first.data['count'], 0)
        since = first.data['now']

        old = Post.objects.create(author=self.bob, content_text="old", privacy=PrivacyChoices.PUBLIC)
        Post.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=1))
        Post.objects.create(author=self.bob, content_text="new", privacy=PrivacyChoices.PUBLIC)
        Post.objects.create(author=self.bob, content_text="friends", privacy=PrivacyChoices.FRIENDS_ONLY)
        Post.objects.create(author=self.alice, content_text="mine", privacy=PrivacyChoices.PUBLIC)

        response = self.client.get(url, {"since": since})
        # Bob's new public post only: not the old one, not the friends-only
        # one (not friends), not Alice's own.
        self.assertEqual(response.data['count'], 1)

        self.make_friends(self.alice, self.bob)
        self.assertEqual(self.client.get(url, {"since": since}).data['count'], 2)

    def test_bad_since_is_treated_as_first_visit(self):
        self.client.force_authenticate(self.alice)
        response = self.client.get(reverse('post-feed-new-count'), {"since": "2026-13-45T00:00:00Z"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)


class DeletedPostDetailTest(PostBaseTestCase):
    """The app shows "Post not found" on a 404 — a deleted post must give one."""

    def test_deleted_post_returns_404(self):
        post = Post.objects.create(author=self.alice, content_text="bye", privacy=PrivacyChoices.PUBLIC)
        url = reverse('post-detail', kwargs={'pk': post.id})
        self.client.force_authenticate(self.bob)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)

        Post.objects.filter(pk=post.pk).update(is_deleted=True)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)


class SeparateCommentsTest(PostBaseTestCase):
    """Post detail without comments (?comments=0) + the paginated comment list."""

    def setUp(self):
        super().setUp()
        self.post = Post.objects.create(author=self.alice, content_text="Hello")
        for i in range(25):
            PostComment.objects.create(post=self.post, user=self.bob, comment_text=f"c{i}")
        self.detail_url = reverse("post-detail", kwargs={"pk": self.post.id})
        self.comments_url = reverse("comment-list")

    def test_detail_skips_comments_when_asked(self):
        self.client.force_authenticate(self.bob)
        fast = self.client.get(self.detail_url, {"comments": "0"})
        self.assertEqual(fast.status_code, status.HTTP_200_OK)
        self.assertEqual(fast.data["recent_comments"], [])
        # Older app versions (no flag) still get them.
        full = self.client.get(self.detail_url)
        self.assertEqual(len(full.data["recent_comments"]), 25)

    def test_comments_are_paginated_newest_first(self):
        self.client.force_authenticate(self.bob)
        page = self.client.get(self.comments_url, {"post_id": str(self.post.id)})
        self.assertEqual(page.status_code, status.HTTP_200_OK)
        self.assertEqual(len(page.data["results"]), 20)
        self.assertEqual(page.data["results"][0]["comment_text"], "c24")
        self.assertIsNotNone(page.data["next"])

    def test_post_id_required(self):
        self.client.force_authenticate(self.bob)
        response = self.client.get(self.comments_url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_private_post_comments_hidden(self):
        self.post.privacy = PrivacyChoices.PRIVATE
        self.post.save(update_fields=["privacy"])
        self.client.force_authenticate(self.bob)
        response = self.client.get(self.comments_url, {"post_id": str(self.post.id)})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unknown_post(self):
        self.client.force_authenticate(self.bob)
        response = self.client.get(self.comments_url, {"post_id": "not-a-uuid"})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
