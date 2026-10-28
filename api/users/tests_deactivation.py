"""Deactivating an account: hidden from others until the user signs in again."""

from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from api.friends.models import Friendship
from api.post.models import Post
from api.users.models import User

PASSWORD = "Str0ngPass!word"


def _user(email, **extra):
    return User.objects.create_user(
        email=email, password=PASSWORD, username=email.split("@")[0],
        is_verified=True, is_email_verified=True, first_name=email.split("@")[0].title(), **extra,
    )


@patch("rest_framework.views.APIView.check_throttles", lambda self, request: None)
class DeactivationTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.me = _user("me@example.com")
        self.friend = _user("friend@example.com")
        Friendship.objects.create(sender=self.me, receiver=self.friend)
        self.post = Post.objects.create(author=self.me, content_text="hello")

    def _deactivate(self):
        self.client.force_authenticate(self.me)
        response = self.client.post(reverse("user-deactivate"))
        self.client.force_authenticate(None)
        self.me.refresh_from_db()
        return response

    def test_deactivate(self):
        response = self._deactivate()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(self.me.is_deactivated)
        self.assertIsNotNone(self.me.deactivated_at)
        self.assertTrue(Post.objects.filter(id=self.post.id).exists())  # nothing deleted

    def test_hidden_from_others(self):
        self._deactivate()
        self.client.force_authenticate(self.friend)
        friends = self.client.get(reverse("list-friends"))
        self.assertEqual(friends.data["results"], [])
        feed = self.client.get(reverse("post-feed"))
        self.assertNotIn(str(self.post.id), [str(p["id"]) for p in feed.data["results"]])
        detail = self.client.get(reverse("post-detail", kwargs={"pk": self.post.id}))
        self.assertIn(detail.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))

    def test_old_session_is_refused(self):
        login = self.client.post(reverse("login"), {"email_or_phone": "me@example.com", "password": PASSWORD})
        token = login.data["data"]["access_token"]
        self._deactivate()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = self.client.get(reverse("list-friends"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_signing_in_reactivates(self):
        self._deactivate()
        response = self.client.post(reverse("login"), {"email_or_phone": "me@example.com", "password": PASSWORD})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["data"]["reactivated"])
        self.me.refresh_from_db()
        self.assertFalse(self.me.is_deactivated)

    def test_normal_login_is_not_a_reactivation(self):
        response = self.client.post(reverse("login"), {"email_or_phone": "me@example.com", "password": PASSWORD})
        self.assertFalse(response.data["data"]["reactivated"])
