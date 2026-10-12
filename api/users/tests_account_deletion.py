from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()

NO_MONEY = {
    "available_balance": Decimal("0"),
    "pending_balance": Decimal("0"),
    "held_amount": Decimal("0"),
}


@mock.patch("api.tip.services.get_full_balance_summary", return_value=NO_MONEY)
class AccountDeletionTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="leaver@test.com", username="leaver", phone="10000099",
            password="pass", is_verified=True,
        )
        self.other = User.objects.create_user(
            email="stay@test.com", username="stay", phone="10000098",
            password="pass", is_verified=True,
        )
        self.url = reverse("user-detail")
        self.client.force_authenticate(self.user)

    def test_delete_removes_account_and_content(self, _balance):
        from api.post.models import Post
        from api.story.models import Story

        Post.objects.create(author=self.user, content_text="bye")
        Story.objects.create(
            author=self.user, text_content="bye", expires_at=timezone.now() + timedelta(hours=1)
        )

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(User.objects.filter(id=self.user.id).exists())
        self.assertEqual(Post.objects.filter(content_text="bye").count(), 0)
        self.assertEqual(Story.objects.filter(text_content="bye").count(), 0)
        self.assertTrue(User.objects.filter(id=self.other.id).exists())

    def test_tips_are_kept_without_the_user(self, _balance):
        from api.tip.models import Tip

        tip = Tip.objects.create(
            tipper=self.user, recipient=self.other,
            amount=Decimal("10.00"), commission_percentage=Decimal("10.00"),
            commission_amount=Decimal("1.00"),
            recipient_amount=Decimal("9.00"),
        )
        self.client.delete(self.url)
        tip.refresh_from_db()
        self.assertIsNone(tip.tipper)
        self.assertEqual(tip.recipient, self.other)

    def test_blocked_while_money_is_left(self, balance):
        balance.return_value = {**NO_MONEY, "available_balance": Decimal("12.50")}
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("12.50", response.data["message"])
        self.assertTrue(User.objects.filter(id=self.user.id).exists())

    def test_firebase_login_is_deleted(self, _balance):
        self.user.firebase_uid = "fb-123"
        self.user.save(update_fields=["firebase_uid"])
        with mock.patch("firebase_admin.auth.delete_user") as delete_user:
            self.client.delete(self.url)
        delete_user.assert_called_once_with("fb-123")

    def test_requires_login(self, _balance):
        self.client.force_authenticate(None)
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertTrue(User.objects.filter(id=self.user.id).exists())
