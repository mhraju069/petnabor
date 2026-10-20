from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
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


@mock.patch("api.tip.services.get_full_balance_summary", return_value=NO_MONEY)
class WebAccountDeletionTest(TestCase):
    """The public delete-account page (works without the app)."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()  # rate-limit counters
        self.user = User.objects.create_user(
            email="webleaver@test.com", username="webleaver", phone="10000097",
            password="pass", is_verified=True,
        )
        self.url = reverse("web-delete-account")

    def _link_for(self, email, mock_send):
        response = self.client.post(self.url, {"email": email})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Check your email")
        if not mock_send.called:
            return None
        html = mock_send.call_args.args[1]
        start = html.index("/delete-account/confirm/?token=")
        return html[start:html.index('"', start)]

    @mock.patch("api.users.web_account_deletion.EmailService.send_html_email")
    def test_full_flow_deletes_account(self, mock_send, _balance):
        link = self._link_for("WebLeaver@test.com", mock_send)
        self.assertIsNotNone(link)
        self.assertEqual(mock_send.call_args.args[2], ["webleaver@test.com"])

        # Opening the link only asks for confirmation (mail scanners open links).
        response = self.client.get(link)
        self.assertContains(response, "Yes, delete my account")
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

        token = link.split("token=", 1)[1]
        response = self.client.post(reverse("web-delete-account-confirm"), {"token": token})
        self.assertContains(response, "Your account has been deleted")
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())

        # The link can't be used again.
        response = self.client.get(link)
        self.assertContains(response, "This link doesn")

    @mock.patch("api.users.web_account_deletion.EmailService.send_html_email")
    def test_unknown_email_gets_same_answer_and_no_email(self, mock_send, _balance):
        self.assertIsNone(self._link_for("nobody@test.com", mock_send))
        mock_send.assert_not_called()

    @mock.patch("api.users.web_account_deletion.EmailService.send_html_email")
    def test_staff_accounts_cannot_be_deleted_here(self, mock_send, _balance):
        self.user.is_staff = True
        self.user.save()
        self.assertIsNone(self._link_for("webleaver@test.com", mock_send))

    def test_tampered_token_is_rejected(self, _balance):
        response = self.client.get(reverse("web-delete-account-confirm") + "?token=abc")
        self.assertContains(response, "This link doesn")
        response = self.client.post(reverse("web-delete-account-confirm"), {"token": "abc"})
        self.assertContains(response, "This link doesn")
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    @mock.patch("api.users.web_account_deletion.EmailService.send_html_email")
    def test_requests_are_rate_limited(self, mock_send, _balance):
        for _ in range(5):
            self.client.post(self.url, {"email": "webleaver@test.com"})
        response = self.client.post(self.url, {"email": "webleaver@test.com"})
        self.assertContains(response, "Too many requests")
        self.assertEqual(mock_send.call_count, 5)


class ChildSafetyStandardsPageTest(TestCase):
    """Public page Google Play's Child Safety Standards declaration links to."""

    @override_settings(CHILD_SAFETY_EMAIL="safety@example.com")
    def test_public_and_has_what_google_checks(self):
        response = self.client.get(reverse("child-safety"))  # no login
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pet Nabor Child Safety Standards")
        self.assertContains(response, "child sexual abuse and exploitation (CSAE)")
        self.assertContains(response, "zero tolerance")
        self.assertContains(response, "safety@example.com")
        self.assertEqual(response["Content-Type"].split(";")[0], "text/html")
