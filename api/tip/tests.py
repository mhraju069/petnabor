"""
Tip System Tests.

Full coverage of:
- Onboarding flow
- Tip creation (PaymentIntent)
- Webhook: payment_intent.succeeded, payment_intent.payment_failed,
           payout.paid, payout.failed, account.updated, charge.refunded,
           invalid signature
- Withdrawal: success, insufficient balance, unverified account
- Commission calculation edge cases
- Admin-controlled commission config
- Tip history filters
"""

import json
import uuid
from decimal import Decimal
from unittest.mock import patch, MagicMock, PropertyMock

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status

from api.users.models import User
from api.meeting.models import Meeting
from .models import (
    TipSettings,
    StripeConnectAccount,
    Tip,
    TipStatus,
    TipWithdrawal,
    WithdrawalStatus,
)
from . import services


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def make_user(email, password="testpass123"):
    u = User.objects.create_user(email=email, password=password)
    return u


def make_connect_account(user, account_id=None, verified=True):
    if account_id is None:
        account_id = f"acct_{uuid.uuid4().hex[:12]}"
    return StripeConnectAccount.objects.create(
        user=user,
        stripe_account_id=account_id,
        is_onboarding_complete=verified,
        is_charges_enabled=verified,
        is_payouts_enabled=verified,
    )


def make_tip(tipper, recipient, amount=Decimal("20.00"), status=TipStatus.PENDING):
    tip_settings = TipSettings.get_instance()
    commission_pct = tip_settings.commission_percentage
    commission, recipient_amount = services.calculate_commission(amount, commission_pct)
    return Tip.objects.create(
        tipper=tipper,
        recipient=recipient,
        amount=amount,
        commission_percentage=commission_pct,
        commission_amount=commission,
        recipient_amount=recipient_amount,
        stripe_payment_intent_id=f"pi_{uuid.uuid4().hex[:14]}",
        status=status,
    )


def make_meeting(sender, receiver, status="COMPLETED"):
    return Meeting.objects.create(
        sender=sender,
        receiver=receiver,
        visitor_name="Test Visitor",
        visitor_phone="1234567890",
        visit_date="2025-01-01",
        visit_time="10:00:00",
        reason="social",
        address_street="123 Test St",
        city="Test City",
        state="CA",
        zipcode="90210",
        status=status,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Commission Calculation
# ──────────────────────────────────────────────────────────────────────────────


class CommissionCalculationTests(TestCase):
    """Unit tests for commission math — no Stripe calls needed."""

    def test_default_20_percent(self):
        commission, recipient = services.calculate_commission(
            Decimal("100.00"), Decimal("20.00")
        )
        self.assertEqual(commission, Decimal("20.00"))
        self.assertEqual(recipient, Decimal("80.00"))

    def test_15_percent(self):
        commission, recipient = services.calculate_commission(
            Decimal("100.00"), Decimal("15.00")
        )
        self.assertEqual(commission, Decimal("15.00"))
        self.assertEqual(recipient, Decimal("85.00"))

    def test_rounding_half_up(self):
        """$10.00 * 33.33% = $3.333... → rounds to $3.33"""
        commission, recipient = services.calculate_commission(
            Decimal("10.00"), Decimal("33.33")
        )
        self.assertEqual(commission, Decimal("3.33"))
        self.assertEqual(recipient, Decimal("6.67"))

    def test_zero_commission(self):
        commission, recipient = services.calculate_commission(
            Decimal("50.00"), Decimal("0.00")
        )
        self.assertEqual(commission, Decimal("0.00"))
        self.assertEqual(recipient, Decimal("50.00"))

    def test_100_percent_commission(self):
        commission, recipient = services.calculate_commission(
            Decimal("50.00"), Decimal("100.00")
        )
        self.assertEqual(commission, Decimal("50.00"))
        self.assertEqual(recipient, Decimal("0.00"))

    def test_commission_uses_snapshot_not_live_setting(self):
        """Tip stores commission % at creation time; changing settings doesn't affect it."""
        tip_settings = TipSettings.get_instance()
        tip_settings.commission_percentage = Decimal("20.00")
        tip_settings.save()

        tipper = make_user("tipper@test.com")
        recipient = make_user("recipient@test.com")
        tip = make_tip(tipper, recipient, amount=Decimal("100.00"))
        self.assertEqual(tip.commission_percentage, Decimal("20.00"))

        # Change global setting
        tip_settings.commission_percentage = Decimal("10.00")
        tip_settings.save()

        # Reload: existing tip's snapshot unchanged
        tip.refresh_from_db()
        self.assertEqual(tip.commission_percentage, Decimal("20.00"))


# ──────────────────────────────────────────────────────────────────────────────
# TipSettings Singleton
# ──────────────────────────────────────────────────────────────────────────────


class TipSettingsSingletonTests(TestCase):
    def test_get_instance_creates_defaults(self):
        TipSettings.objects.all().delete()
        instance = TipSettings.get_instance()
        self.assertEqual(TipSettings.objects.count(), 1)
        self.assertEqual(instance.commission_percentage, Decimal("20.00"))

    def test_get_instance_reuses_existing(self):
        TipSettings.objects.all().delete()
        first = TipSettings.get_instance()
        second = TipSettings.get_instance()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(TipSettings.objects.count(), 1)

    def test_commission_change_affects_new_tip_calculation(self):
        tip_settings = TipSettings.get_instance()
        tip_settings.commission_percentage = Decimal("10.00")
        tip_settings.save()

        commission, recipient = services.calculate_commission(
            Decimal("100.00"), Decimal("10.00")
        )
        self.assertEqual(commission, Decimal("10.00"))


# ──────────────────────────────────────────────────────────────────────────────
# Stripe Connect Onboarding
# ──────────────────────────────────────────────────────────────────────────────


@override_settings(
    STRIPE_SECRET_KEY="sk_test_fake",
    STRIPE_PUBLISHABLE_KEY="pk_test_fake",
    STRIPE_WEBHOOK_SECRET="whsec_fake",
)
class ConnectOnboardTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_user("user@test.com")
        self.client.force_authenticate(user=self.user)
        self.url = reverse("tip:connect-onboard")

    @patch("api.tip.services._stripe")
    def test_creates_new_express_account(self, mock_stripe):
        mock_s = MagicMock()
        mock_stripe.return_value = mock_s

        mock_account = MagicMock()
        mock_account.id = "acct_new123"
        mock_s.Account.create.return_value = mock_account

        mock_link = MagicMock()
        mock_link.url = "https://connect.stripe.com/setup/e/acct_new123/fake"
        mock_s.AccountLink.create.return_value = mock_link

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("onboarding_url", response.data)
        self.assertTrue(StripeConnectAccount.objects.filter(user=self.user).exists())

    @patch("api.tip.services._stripe")
    def test_returns_existing_account_link(self, mock_stripe):
        """If user already has a connect account, return fresh link without creating new."""
        make_connect_account(self.user, account_id="acct_existing")
        mock_s = MagicMock()
        mock_stripe.return_value = mock_s

        mock_link = MagicMock()
        mock_link.url = "https://connect.stripe.com/fresh-link"
        mock_s.AccountLink.create.return_value = mock_link

        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Account.create should NOT be called again
        mock_s.Account.create.assert_not_called()
        self.assertEqual(StripeConnectAccount.objects.filter(user=self.user).count(), 1)

    def test_unauthenticated_returns_401(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# ──────────────────────────────────────────────────────────────────────────────
# Connect Status
# ──────────────────────────────────────────────────────────────────────────────


@override_settings(
    STRIPE_SECRET_KEY="sk_test_fake",
    STRIPE_WEBHOOK_SECRET="whsec_fake",
)
class ConnectStatusTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_user("status@test.com")
        self.client.force_authenticate(user=self.user)
        self.url = reverse("tip:connect-status")

    def test_no_account_returns_404(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("api.tip.services._stripe")
    def test_syncs_account_from_stripe(self, mock_stripe):
        connect = make_connect_account(self.user, verified=False)
        mock_s = MagicMock()
        mock_stripe.return_value = mock_s

        mock_account = MagicMock()
        mock_account.charges_enabled = True
        mock_account.payouts_enabled = True
        mock_account.details_submitted = True
        mock_s.Account.retrieve.return_value = mock_account

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        connect.refresh_from_db()
        self.assertTrue(connect.is_charges_enabled)
        self.assertTrue(connect.is_onboarding_complete)


# ──────────────────────────────────────────────────────────────────────────────
# Send Tip
# ──────────────────────────────────────────────────────────────────────────────


@override_settings(
    STRIPE_SECRET_KEY="sk_test_fake",
    STRIPE_PUBLISHABLE_KEY="pk_test_fake",
    STRIPE_WEBHOOK_SECRET="whsec_fake",
)
class SendTipTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tipper = make_user("tipper@test.com")
        self.recipient = make_user("recipient@test.com")
        self.connect = make_connect_account(self.recipient, verified=True)
        self.client.force_authenticate(user=self.tipper)
        self.url = reverse("tip:send-tip")

    def _post(self, data):
        return self.client.post(self.url, data, format="json")

    @patch("api.tip.services._stripe")
    def test_send_tip_success(self, mock_stripe):
        mock_s = MagicMock()
        mock_stripe.return_value = mock_s
        mock_intent = MagicMock()
        mock_intent.id = "pi_test123"
        mock_intent.client_secret = "pi_test123_secret_xxx"
        mock_s.PaymentIntent.create.return_value = mock_intent

        response = self._post({
            "recipient_id": str(self.recipient.id),
            "amount": "20.00",
            "note": "Great service!",
        })

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("client_secret", response.data)
        self.assertIn("publishable_key", response.data)
        self.assertIn("tip", response.data)

        tip = Tip.objects.get(stripe_payment_intent_id="pi_test123")
        self.assertEqual(tip.tipper, self.tipper)
        self.assertEqual(tip.recipient, self.recipient)
        self.assertEqual(tip.amount, Decimal("20.00"))
        self.assertEqual(tip.commission_percentage, Decimal("20.00"))
        self.assertEqual(tip.commission_amount, Decimal("4.00"))
        self.assertEqual(tip.recipient_amount, Decimal("16.00"))
        self.assertEqual(tip.status, TipStatus.PENDING)

    @patch("api.tip.services._stripe")
    def test_send_tip_idempotency(self, mock_stripe):
        mock_s = MagicMock()
        mock_stripe.return_value = mock_s
        mock_intent = MagicMock()
        mock_intent.id = "pi_idem123"
        mock_intent.client_secret = "pi_idem123_secret_xxx"
        mock_s.PaymentIntent.create.return_value = mock_intent

        import uuid
        idem_key = str(uuid.uuid4())

        # First request
        response1 = self._post({
            "recipient_id": str(self.recipient.id),
            "amount": "20.00",
            "idempotency_key": idem_key,
        })
        self.assertEqual(response1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Tip.objects.count(), 1)

        # Ensure idempotency_key was passed to Stripe
        kwargs = mock_s.PaymentIntent.create.call_args.kwargs
        self.assertEqual(kwargs.get("idempotency_key"), f"tip_intent_{idem_key}")

        # Second request with identical idempotency_key
        # Since Stripe returns the same mock intent ID, Tip get_or_create should prevent duplicates
        response2 = self._post({
            "recipient_id": str(self.recipient.id),
            "amount": "20.00",
            "idempotency_key": idem_key,
        })
        self.assertEqual(response2.status_code, status.HTTP_201_CREATED)
        
        # Still exactly 1 tip in the DB
        self.assertEqual(Tip.objects.count(), 1)
        self.assertEqual(response1.data["tip"]["id"], response2.data["tip"]["id"])

    @patch("api.tip.services._stripe")
    def test_commission_correct_in_stripe_call(self, mock_stripe):
        """Verify application_fee_amount in cents = commission amount."""
        mock_s = MagicMock()
        mock_stripe.return_value = mock_s
        mock_intent = MagicMock()
        mock_intent.id = "pi_fee_test"
        mock_intent.client_secret = "secret"
        mock_s.PaymentIntent.create.return_value = mock_intent

        self._post({
            "recipient_id": str(self.recipient.id),
            "amount": "50.00",
        })

        call_kwargs = mock_s.PaymentIntent.create.call_args.kwargs
        # 20% of $50 = $10 = 1000 cents
        self.assertEqual(call_kwargs["application_fee_amount"], 1000)
        self.assertEqual(call_kwargs["amount"], 5000)  # $50 in cents
        self.assertEqual(call_kwargs["transfer_data"]["destination"], self.connect.stripe_account_id)

    def test_self_tip_rejected(self):
        response = self._post({
            "recipient_id": str(self.tipper.id),
            "amount": "10.00",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cannot tip yourself", response.data["detail"])

    def test_recipient_not_found(self):
        response = self._post({
            "recipient_id": str(uuid.uuid4()),
            "amount": "10.00",
        })
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("api.tip.services._stripe")
    @patch("api.tip.services.send_notification")
    def test_recipient_no_connect_account_creates_held_tip(self, mock_notify, mock_stripe):
        """When recipient has no Stripe account, tip is created with HELD status."""
        mock_s = MagicMock()
        mock_stripe.return_value = mock_s
        mock_intent = MagicMock()
        mock_intent.id = "pi_held_no_account"
        mock_intent.client_secret = "secret_held"
        mock_s.PaymentIntent.create.return_value = mock_intent

        other = make_user("noconnect@test.com")
        response = self._post({
            "recipient_id": str(other.id),
            "amount": "10.00",
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # Tip must be HELD, not blocked
        tip = Tip.objects.get(stripe_payment_intent_id="pi_held_no_account")
        self.assertEqual(tip.status, TipStatus.HELD)
        # PaymentIntent created WITHOUT transfer_data
        call_kwargs = mock_s.PaymentIntent.create.call_args.kwargs
        self.assertNotIn("transfer_data", call_kwargs)
        self.assertNotIn("application_fee_amount", call_kwargs)
        # Recipient notified
        mock_notify.assert_called_once()
        # Serializer flag
        self.assertTrue(response.data["tip"]["is_held"])

    @patch("api.tip.services._stripe")
    @patch("api.tip.services.send_notification")
    def test_recipient_unverified_account_creates_held_tip(self, mock_notify, mock_stripe):
        """When recipient's account is unverified, tip is still held not rejected."""
        mock_s = MagicMock()
        mock_stripe.return_value = mock_s
        mock_intent = MagicMock()
        mock_intent.id = "pi_held_unverified"
        mock_intent.client_secret = "secret_held2"
        mock_s.PaymentIntent.create.return_value = mock_intent

        unverified = make_user("unverified@test.com")
        make_connect_account(unverified, verified=False)
        response = self._post({
            "recipient_id": str(unverified.id),
            "amount": "10.00",
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tip = Tip.objects.get(stripe_payment_intent_id="pi_held_unverified")
        self.assertEqual(tip.status, TipStatus.HELD)
        self.assertTrue(response.data["tip"]["is_held"])

    def test_amount_below_minimum_rejected(self):
        tip_settings = TipSettings.get_instance()
        tip_settings.minimum_tip_amount = Decimal("5.00")
        tip_settings.save()

        response = self._post({
            "recipient_id": str(self.recipient.id),
            "amount": "1.00",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_amount_above_maximum_rejected(self):
        tip_settings = TipSettings.get_instance()
        tip_settings.maximum_tip_amount = Decimal("100.00")
        tip_settings.save()

        response = self._post({
            "recipient_id": str(self.recipient.id),
            "amount": "500.00",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("api.tip.services._stripe")
    def test_tip_with_valid_meeting(self, mock_stripe):
        mock_s = MagicMock()
        mock_stripe.return_value = mock_s
        mock_intent = MagicMock()
        mock_intent.id = "pi_meeting_test"
        mock_intent.client_secret = "secret_meeting"
        mock_s.PaymentIntent.create.return_value = mock_intent

        meeting = make_meeting(self.tipper, self.recipient)
        response = self._post({
            "recipient_id": str(self.recipient.id),
            "meeting_id": str(meeting.id),
            "amount": "15.00",
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tip = Tip.objects.get(stripe_payment_intent_id="pi_meeting_test")
        self.assertEqual(tip.meeting, meeting)

    def test_nonexistent_meeting_rejected(self):
        response = self._post({
            "recipient_id": str(self.recipient.id),
            "meeting_id": str(uuid.uuid4()),
            "amount": "15.00",
        })
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(user=None)
        response = self._post({
            "recipient_id": str(self.recipient.id),
            "amount": "10.00",
        })
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# ──────────────────────────────────────────────────────────────────────────────
# Tip History
# ──────────────────────────────────────────────────────────────────────────────


class TipHistoryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user1 = make_user("hist1@test.com")
        self.user2 = make_user("hist2@test.com")
        self.client.force_authenticate(user=self.user1)
        self.url = reverse("tip:tip-history")

    def test_history_returns_sent_and_received(self):
        make_tip(self.user1, self.user2)  # sent
        make_tip(self.user2, self.user1)  # received
        make_tip(self.user2, make_user("other@test.com"))  # unrelated

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_filter_sent(self):
        make_tip(self.user1, self.user2)
        make_tip(self.user2, self.user1)
        response = self.client.get(self.url, {"direction": "sent"})
        self.assertEqual(len(response.data), 1)
        self.assertEqual(str(response.data[0]["tipper"]["id"]), str(self.user1.id))

    def test_filter_received(self):
        make_tip(self.user1, self.user2)
        make_tip(self.user2, self.user1)
        response = self.client.get(self.url, {"direction": "received"})
        self.assertEqual(len(response.data), 1)
        self.assertEqual(str(response.data[0]["recipient"]["id"]), str(self.user1.id))

    def test_filter_by_status(self):
        make_tip(self.user1, self.user2, status=TipStatus.SUCCEEDED)
        make_tip(self.user1, self.user2, status=TipStatus.FAILED)
        response = self.client.get(self.url, {"status": "succeeded"})
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["status"], "succeeded")


# ──────────────────────────────────────────────────────────────────────────────
# Webhook Tests
# ──────────────────────────────────────────────────────────────────────────────


@override_settings(
    STRIPE_SECRET_KEY="sk_test_fake",
    STRIPE_WEBHOOK_SECRET="whsec_fake",
)
class WebhookTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("tip:stripe-webhook")
        self.tipper = make_user("whtipper@test.com")
        self.recipient = make_user("whrecipient@test.com")
        self.connect = make_connect_account(self.recipient)
        self.tip = make_tip(self.tipper, self.recipient)

    def _post_webhook(self, event_type, data_object, mock_construct):
        """Helper to post a webhook event and simulate signature verification."""
        mock_event = {
            "id": f"evt_{uuid.uuid4().hex[:16]}",
            "type": event_type,
            "data": {"object": data_object},
        }
        mock_construct.return_value = mock_event
        payload = json.dumps({"type": event_type}).encode()
        return self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=fakesig",
        )

    @patch("api.tip.services.construct_stripe_event")
    def test_payment_intent_succeeded(self, mock_construct):
        response = self._post_webhook(
            "payment_intent.succeeded",
            {
                "id": self.tip.stripe_payment_intent_id,
                "latest_charge": "ch_test123",
            },
            mock_construct,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.tip.refresh_from_db()
        self.assertEqual(self.tip.status, TipStatus.SUCCEEDED)
        self.assertEqual(self.tip.stripe_charge_id, "ch_test123")

    @patch("api.tip.services.construct_stripe_event")
    def test_payment_intent_succeeded_idempotent(self, mock_construct):
        """Second webhook for same payment_intent should be safe no-op."""
        self.tip.status = TipStatus.SUCCEEDED
        self.tip.stripe_charge_id = "ch_already"
        self.tip.save()

        self._post_webhook(
            "payment_intent.succeeded",
            {"id": self.tip.stripe_payment_intent_id, "latest_charge": "ch_duplicate"},
            mock_construct,
        )
        self.tip.refresh_from_db()
        self.assertEqual(self.tip.stripe_charge_id, "ch_already")  # unchanged

    @patch("api.tip.services.construct_stripe_event")
    def test_payment_intent_failed(self, mock_construct):
        response = self._post_webhook(
            "payment_intent.payment_failed",
            {"id": self.tip.stripe_payment_intent_id},
            mock_construct,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.tip.refresh_from_db()
        self.assertEqual(self.tip.status, TipStatus.FAILED)

    @patch("api.tip.services.construct_stripe_event")
    def test_payment_intent_cancelled(self, mock_construct):
        response = self._post_webhook(
            "payment_intent.canceled",
            {"id": self.tip.stripe_payment_intent_id},
            mock_construct,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.tip.refresh_from_db()
        self.assertEqual(self.tip.status, TipStatus.CANCELLED)

    @patch("api.tip.services.construct_stripe_event")
    def test_charge_refunded(self, mock_construct):
        self.tip.status = TipStatus.SUCCEEDED
        self.tip.stripe_charge_id = "ch_refund_test"
        self.tip.save()

        response = self._post_webhook(
            "charge.refunded",
            {"id": "ch_refund_test"},
            mock_construct,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.tip.refresh_from_db()
        self.assertEqual(self.tip.status, TipStatus.REFUNDED)

    @patch("api.tip.services.construct_stripe_event")
    def test_account_updated_marks_verified(self, mock_construct):
        self.connect.is_charges_enabled = False
        self.connect.is_payouts_enabled = False
        self.connect.is_onboarding_complete = False
        self.connect.save()

        response = self._post_webhook(
            "account.updated",
            {
                "id": self.connect.stripe_account_id,
                "charges_enabled": True,
                "payouts_enabled": True,
                "details_submitted": True,
            },
            mock_construct,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.connect.refresh_from_db()
        self.assertTrue(self.connect.is_charges_enabled)
        self.assertTrue(self.connect.is_payouts_enabled)
        self.assertTrue(self.connect.is_onboarding_complete)

    @patch("api.tip.services.construct_stripe_event")
    def test_payout_paid(self, mock_construct):
        withdrawal = TipWithdrawal.objects.create(
            user=self.recipient,
            connect_account=self.connect,
            amount=Decimal("50.00"),
            stripe_payout_id="po_paid_test",
            status=WithdrawalStatus.PENDING,
        )
        response = self._post_webhook(
            "payout.paid",
            {"id": "po_paid_test"},
            mock_construct,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.status, WithdrawalStatus.PAID)

    @patch("api.tip.services.construct_stripe_event")
    def test_payout_failed(self, mock_construct):
        withdrawal = TipWithdrawal.objects.create(
            user=self.recipient,
            connect_account=self.connect,
            amount=Decimal("50.00"),
            stripe_payout_id="po_fail_test",
            status=WithdrawalStatus.PENDING,
        )
        response = self._post_webhook(
            "payout.failed",
            {
                "id": "po_fail_test",
                "failure_message": "Your bank declined the payout.",
            },
            mock_construct,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.status, WithdrawalStatus.FAILED)
        self.assertIn("declined", withdrawal.failure_message)

    @patch("api.tip.services.construct_stripe_event")
    def test_unhandled_event_returns_200(self, mock_construct):
        """Unknown events should be silently ignored with 200 OK."""
        response = self._post_webhook(
            "customer.created",
            {"id": "cus_unknown"},
            mock_construct,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_missing_signature_header_returns_400(self):
        payload = json.dumps({"type": "payment_intent.succeeded"}).encode()
        response = self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
            # No HTTP_STRIPE_SIGNATURE
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("api.tip.services.construct_stripe_event")
    def test_invalid_signature_returns_400(self, mock_construct):
        """Stripe signature verification failure must return 400."""
        import stripe as stripe_lib
        mock_construct.side_effect = stripe_lib.error.SignatureVerificationError(
            "No signatures found", "fake_sig"
        )
        payload = json.dumps({"type": "payment_intent.succeeded"}).encode()
        response = self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=invalid",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("api.tip.services.construct_stripe_event")
    def test_webhook_unknown_payment_intent_logs_and_returns_200(self, mock_construct):
        """Webhook for unknown PI should not crash."""
        response = self._post_webhook(
            "payment_intent.succeeded",
            {"id": "pi_nonexistent_xyz", "latest_charge": None},
            mock_construct,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


# ──────────────────────────────────────────────────────────────────────────────
# Balance & Withdrawal
# ──────────────────────────────────────────────────────────────────────────────


@override_settings(
    STRIPE_SECRET_KEY="sk_test_fake",
    STRIPE_WEBHOOK_SECRET="whsec_fake",
)
class WithdrawalTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_user("withdraw@test.com")
        self.connect = make_connect_account(self.user, verified=True)
        self.client.force_authenticate(user=self.user)
        self.withdraw_url = reverse("tip:withdraw")
        self.balance_url = reverse("tip:tip-balance")
        self.history_url = reverse("tip:withdraw-history")

    @patch("api.tip.services._stripe")
    def test_balance_returns_available(self, mock_stripe):
        mock_s = MagicMock()
        mock_stripe.return_value = mock_s
        mock_s.Balance.retrieve.return_value = {
            "available": [{"currency": "usd", "amount": 5000}]
        }
        response = self.client.get(self.balance_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Stripe returns amount in cents as int; service converts to Decimal string
        self.assertIn(response.data["available_balance"], ["50", "50.00"])

    @patch("api.tip.services._stripe")
    def test_withdrawal_success(self, mock_stripe):
        mock_s = MagicMock()
        mock_stripe.return_value = mock_s

        # Balance check: $100 available
        mock_s.Balance.retrieve.return_value = {
            "available": [{"currency": "usd", "amount": 10000}]
        }
        mock_payout = MagicMock()
        mock_payout.id = "po_test_withdraw"
        mock_s.Payout.create.return_value = mock_payout

        response = self.client.post(self.withdraw_url, {"amount": "50.00"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["stripe_payout_id"], "po_test_withdraw")
        self.assertEqual(response.data["status"], "pending")

    @patch("api.tip.services._stripe")
    def test_withdrawal_insufficient_balance(self, mock_stripe):
        mock_s = MagicMock()
        mock_stripe.return_value = mock_s
        mock_s.Balance.retrieve.return_value = {
            "available": [{"currency": "usd", "amount": 500}]  # $5.00
        }

        response = self.client.post(self.withdraw_url, {"amount": "50.00"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Insufficient balance", response.data["detail"])

    def test_withdrawal_no_connect_account(self):
        user = make_user("noconnect2@test.com")
        self.client.force_authenticate(user=user)
        response = self.client.post(self.withdraw_url, {"amount": "50.00"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("connect your Stripe account", response.data["detail"])

    def test_withdrawal_unverified_account(self):
        user = make_user("unverified2@test.com")
        make_connect_account(user, verified=False)
        self.client.force_authenticate(user=user)
        response = self.client.post(self.withdraw_url, {"amount": "50.00"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not fully verified", response.data["detail"])

    def test_withdrawal_below_minimum(self):
        tip_settings = TipSettings.get_instance()
        tip_settings.minimum_withdrawal_amount = Decimal("20.00")
        tip_settings.save()

        response = self.client.post(self.withdraw_url, {"amount": "5.00"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_withdrawal_history(self):
        TipWithdrawal.objects.create(
            user=self.user,
            connect_account=self.connect,
            amount=Decimal("30.00"),
            stripe_payout_id="po_hist1",
            status=WithdrawalStatus.PAID,
        )
        TipWithdrawal.objects.create(
            user=self.user,
            connect_account=self.connect,
            amount=Decimal("20.00"),
            stripe_payout_id="po_hist2",
            status=WithdrawalStatus.PENDING,
        )
        response = self.client.get(self.history_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_withdrawal_history_isolated_to_user(self):
        """Another user's withdrawals should not appear."""
        other_user = make_user("otherwithdraw@test.com")
        other_connect = make_connect_account(other_user, account_id="acct_other456")
        TipWithdrawal.objects.create(
            user=other_user,
            connect_account=other_connect,
            amount=Decimal("40.00"),
            stripe_payout_id="po_other",
            status=WithdrawalStatus.PAID,
        )
        response = self.client.get(self.history_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)


# ──────────────────────────────────────────────────────────────────────────────
# Tip Settings API
# ──────────────────────────────────────────────────────────────────────────────


class TipSettingsAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_user("settingsapi@test.com")
        self.client.force_authenticate(user=self.user)
        self.url = reverse("tip:tip-settings")

    def test_returns_settings(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("commission_percentage", response.data)
        self.assertIn("minimum_tip_amount", response.data)
        self.assertIn("maximum_tip_amount", response.data)

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# ──────────────────────────────────────────────────────────────────────────────
# Hold & Release Tests
# ──────────────────────────────────────────────────────────────────────────────


@override_settings(
    STRIPE_SECRET_KEY="sk_test_fake",
    STRIPE_PUBLISHABLE_KEY="pk_test_fake",
    STRIPE_WEBHOOK_SECRET="whsec_fake",
)
class HoldAndReleaseTests(TestCase):
    """
    Tests for the payment hold & release system.

    Flow:
    1. Tipper sends a tip to a recipient with no Stripe account.
    2. Tip created as HELD, PaymentIntent has no transfer_data.
    3. Webhook: payment_intent.succeeded -> charge_id stored, status stays HELD.
    4. Recipient connects Stripe -> account.updated webhook fires.
    5. release_held_tips() runs, Stripe Transfer created, status -> SUCCEEDED.
    """

    def setUp(self):
        self.tipper = make_user("hold_tipper@test.com")
        self.recipient = make_user("hold_recipient@test.com")
        self.client = APIClient()
        self.client.force_authenticate(user=self.tipper)
        self.webhook_url = reverse("tip:stripe-webhook")
        self.send_url = reverse("tip:send-tip")

    def _make_held_tip(self, charge_id=None):
        """Create a HELD tip directly in DB (bypasses Stripe)."""
        tip_settings = TipSettings.get_instance()
        commission_pct = tip_settings.commission_percentage
        commission, recipient_amount = services.calculate_commission(
            Decimal("20.00"), commission_pct
        )
        return Tip.objects.create(
            tipper=self.tipper,
            recipient=self.recipient,
            amount=Decimal("20.00"),
            commission_percentage=commission_pct,
            commission_amount=commission,
            recipient_amount=recipient_amount,
            stripe_payment_intent_id=f"pi_{uuid.uuid4().hex[:14]}",
            stripe_charge_id=charge_id,
            status=TipStatus.HELD,
        )

    def _post_webhook(self, event_type, data_object, mock_construct):
        mock_event = {
            "id": f"evt_{uuid.uuid4().hex[:16]}",
            "type": event_type,
            "data": {"object": data_object},
        }
        mock_construct.return_value = mock_event
        payload = json.dumps({"type": event_type}).encode()
        return self.client.post(
            self.webhook_url,
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=fakesig",
        )

    # ------------------------------------------------------------------
    # 1. PaymentIntent creation: held flow vs direct flow
    # ------------------------------------------------------------------

    @patch("api.tip.services._stripe")
    @patch("api.tip.services.send_notification")
    def test_held_tip_created_when_no_connect_account(self, mock_notify, mock_stripe):
        """Tip to user without Stripe account -> HELD, no transfer_data sent to Stripe."""
        mock_s = MagicMock()
        mock_stripe.return_value = mock_s
        mock_intent = MagicMock()
        mock_intent.id = "pi_hold_001"
        mock_intent.client_secret = "secret_hold_001"
        mock_s.PaymentIntent.create.return_value = mock_intent

        response = self.client.post(self.send_url, {
            "recipient_id": str(self.recipient.id),
            "amount": "20.00",
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tip = Tip.objects.get(stripe_payment_intent_id="pi_hold_001")
        self.assertEqual(tip.status, TipStatus.HELD)

        # Stripe must NOT receive transfer_data or application_fee_amount
        call_kwargs = mock_s.PaymentIntent.create.call_args.kwargs
        self.assertNotIn("transfer_data", call_kwargs)
        self.assertNotIn("application_fee_amount", call_kwargs)
        self.assertEqual(call_kwargs["metadata"]["flow"], "held")

        # Recipient must be notified
        mock_notify.assert_called_once()

    @patch("api.tip.services._stripe")
    def test_direct_tip_created_when_account_verified(self, mock_stripe):
        """Tip to verified recipient -> PENDING with transfer_data."""
        connect = make_connect_account(self.recipient, verified=True)
        mock_s = MagicMock()
        mock_stripe.return_value = mock_s
        mock_intent = MagicMock()
        mock_intent.id = "pi_direct_001"
        mock_intent.client_secret = "secret_direct"
        mock_s.PaymentIntent.create.return_value = mock_intent

        response = self.client.post(self.send_url, {
            "recipient_id": str(self.recipient.id),
            "amount": "20.00",
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tip = Tip.objects.get(stripe_payment_intent_id="pi_direct_001")
        self.assertEqual(tip.status, TipStatus.PENDING)
        self.assertFalse(response.data["tip"]["is_held"])

        call_kwargs = mock_s.PaymentIntent.create.call_args.kwargs
        self.assertIn("transfer_data", call_kwargs)
        self.assertEqual(call_kwargs["transfer_data"]["destination"], connect.stripe_account_id)
        self.assertIn("application_fee_amount", call_kwargs)
        self.assertEqual(call_kwargs["metadata"]["flow"], "direct")

    # ------------------------------------------------------------------
    # 2. Webhook: payment confirmed on a HELD tip
    # ------------------------------------------------------------------

    @patch("api.tip.services.construct_stripe_event")
    @patch("api.tip.services.send_notification")
    def test_webhook_confirms_held_tip_without_marking_succeeded(
        self, mock_notify, mock_construct
    ):
        """
        payment_intent.succeeded for a HELD tip:
        - charge_id must be saved
        - status must remain HELD (not SUCCEEDED)
        - recipient must be re-notified
        """
        tip = self._make_held_tip()  # no charge_id yet

        response = self._post_webhook(
            "payment_intent.succeeded",
            {
                "id": tip.stripe_payment_intent_id,
                "latest_charge": "ch_held_confirmed",
            },
            mock_construct,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tip.refresh_from_db()
        self.assertEqual(tip.status, TipStatus.HELD)   # still HELD
        self.assertEqual(tip.stripe_charge_id, "ch_held_confirmed")  # charge stored
        mock_notify.assert_called_once()  # re-notification sent

    @patch("api.tip.services.construct_stripe_event")
    def test_webhook_succeeded_on_pending_tip_marks_succeeded(self, mock_construct):
        """Normal PENDING tip -> SUCCEEDED on payment_intent.succeeded."""
        connect = make_connect_account(self.recipient)
        tip = make_tip(self.tipper, self.recipient, status=TipStatus.PENDING)

        self._post_webhook(
            "payment_intent.succeeded",
            {"id": tip.stripe_payment_intent_id, "latest_charge": "ch_direct_ok"},
            mock_construct,
        )

        tip.refresh_from_db()
        self.assertEqual(tip.status, TipStatus.SUCCEEDED)
        self.assertEqual(tip.stripe_charge_id, "ch_direct_ok")

    # ------------------------------------------------------------------
    # 3. transfer_held_tip: service unit test
    # ------------------------------------------------------------------

    @patch("api.tip.services._stripe")
    @patch("api.tip.services.send_notification")
    def test_transfer_held_tip_succeeds(self, mock_notify, mock_stripe):
        """transfer_held_tip creates a Stripe Transfer and marks the tip SUCCEEDED."""
        connect = make_connect_account(self.recipient, account_id="acct_release_test")
        tip = self._make_held_tip(charge_id="ch_to_release")

        mock_s = MagicMock()
        mock_stripe.return_value = mock_s
        mock_transfer = MagicMock()
        mock_transfer.id = "tr_released_001"
        mock_s.Transfer.create.return_value = mock_transfer

        result = services.transfer_held_tip(tip, connect)

        self.assertTrue(result)
        tip.refresh_from_db()
        self.assertEqual(tip.status, TipStatus.SUCCEEDED)
        self.assertEqual(tip.stripe_transfer_id, "tr_released_001")

        # Verify Stripe Transfer call
        call_kwargs = mock_s.Transfer.create.call_args.kwargs
        self.assertEqual(call_kwargs["destination"], "acct_release_test")
        self.assertEqual(call_kwargs["source_transaction"], "ch_to_release")
        # Only recipient_amount transferred (commission stays on platform)
        self.assertEqual(call_kwargs["amount"], int(tip.recipient_amount * 100))

        # Both parties notified
        self.assertEqual(mock_notify.call_count, 2)

    @patch("api.tip.services._stripe")
    @patch("api.tip.services.send_notification")
    def test_transfer_held_tip_returns_false_when_no_charge_id(
        self, mock_notify, mock_stripe
    ):
        """If charge_id is missing, transfer cannot proceed."""
        connect = make_connect_account(self.recipient)
        tip = self._make_held_tip(charge_id=None)  # payment not confirmed yet

        result = services.transfer_held_tip(tip, connect)

        self.assertFalse(result)
        tip.refresh_from_db()
        self.assertEqual(tip.status, TipStatus.HELD)  # unchanged
        mock_stripe.return_value.Transfer.create.assert_not_called()

    @patch("api.tip.services._stripe")
    @patch("api.tip.services.send_notification")
    def test_transfer_held_tip_returns_false_on_stripe_error(
        self, mock_notify, mock_stripe
    ):
        """If Stripe Transfer fails, tip stays HELD and False is returned."""
        import stripe as stripe_lib
        connect = make_connect_account(self.recipient)
        tip = self._make_held_tip(charge_id="ch_fail_transfer")

        mock_s = MagicMock()
        mock_stripe.return_value = mock_s
        mock_s.Transfer.create.side_effect = stripe_lib.error.StripeError("Transfer failed")

        result = services.transfer_held_tip(tip, connect)

        self.assertFalse(result)
        tip.refresh_from_db()
        self.assertEqual(tip.status, TipStatus.HELD)  # unchanged

    # ------------------------------------------------------------------
    # 4. release_held_tips: service unit test
    # ------------------------------------------------------------------

    @patch("api.tip.services._stripe")
    @patch("api.tip.services.send_notification")
    def test_release_held_tips_releases_all_pending(self, mock_notify, mock_stripe):
        """release_held_tips processes all HELD tips with a charge_id."""
        connect = make_connect_account(self.recipient, account_id="acct_batch_release")

        tip1 = self._make_held_tip(charge_id="ch_batch_1")
        tip2 = self._make_held_tip(charge_id="ch_batch_2")
        tip3 = self._make_held_tip(charge_id=None)  # not confirmed yet, must be skipped

        mock_s = MagicMock()
        mock_stripe.return_value = mock_s

        # Each Transfer.create call must return a unique transfer ID
        transfer1 = MagicMock()
        transfer1.id = "tr_batch_1"
        transfer2 = MagicMock()
        transfer2.id = "tr_batch_2"
        mock_s.Transfer.create.side_effect = [transfer1, transfer2]

        services.release_held_tips(self.recipient)

        tip1.refresh_from_db()
        tip2.refresh_from_db()
        tip3.refresh_from_db()

        self.assertEqual(tip1.status, TipStatus.SUCCEEDED)
        self.assertEqual(tip2.status, TipStatus.SUCCEEDED)
        self.assertEqual(tip3.status, TipStatus.HELD)  # skipped, no charge_id
        self.assertEqual(mock_s.Transfer.create.call_count, 2)

    @patch("api.tip.services._stripe")
    def test_release_held_tips_skips_unverified_account(self, mock_stripe):
        """If account is not fully verified, release_held_tips does nothing."""
        make_connect_account(self.recipient, verified=False)
        tip = self._make_held_tip(charge_id="ch_skip_unverified")

        services.release_held_tips(self.recipient)

        tip.refresh_from_db()
        self.assertEqual(tip.status, TipStatus.HELD)  # unchanged
        mock_stripe.return_value.Transfer.create.assert_not_called()

    @patch("api.tip.services._stripe")
    def test_release_held_tips_no_connect_account_is_safe(self, mock_stripe):
        """Calling release for a user with no connect account must not crash."""
        tip = self._make_held_tip(charge_id="ch_no_connect")
        services.release_held_tips(self.recipient)  # should return silently
        tip.refresh_from_db()
        self.assertEqual(tip.status, TipStatus.HELD)

    # ------------------------------------------------------------------
    # 5. account.updated webhook triggers auto-release
    # ------------------------------------------------------------------

    @patch("api.tip.services._stripe")
    @patch("api.tip.services.send_notification")
    @patch("api.tip.services.construct_stripe_event")
    def test_account_updated_triggers_release_on_first_verification(
        self, mock_construct, mock_notify, mock_stripe
    ):
        """
        When account.updated fires and the account becomes verified for the
        first time, all confirmed HELD tips for that user must be released.
        """
        # Account starts unverified
        connect = make_connect_account(self.recipient, account_id="acct_trigger", verified=False)
        tip = self._make_held_tip(charge_id="ch_trigger")

        mock_s = MagicMock()
        mock_stripe.return_value = mock_s
        mock_transfer = MagicMock()
        mock_transfer.id = "tr_triggered"
        mock_s.Transfer.create.return_value = mock_transfer

        # Webhook: account becomes verified
        response = self._post_webhook(
            "account.updated",
            {
                "id": "acct_trigger",
                "charges_enabled": True,
                "payouts_enabled": True,
                "details_submitted": True,
            },
            mock_construct,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tip.refresh_from_db()
        self.assertEqual(tip.status, TipStatus.SUCCEEDED)
        self.assertEqual(tip.stripe_transfer_id, "tr_triggered")
        mock_s.Transfer.create.assert_called_once()

    @patch("api.tip.services._stripe")
    @patch("api.tip.services.construct_stripe_event")
    def test_account_updated_does_not_release_if_already_verified(
        self, mock_construct, mock_stripe
    ):
        """
        If account was already verified before this webhook fired,
        release_held_tips must NOT run again (avoid double transfer).
        """
        # Account already verified
        connect = make_connect_account(self.recipient, account_id="acct_already_ver", verified=True)
        tip = self._make_held_tip(charge_id="ch_already_ver")

        mock_s = MagicMock()
        mock_stripe.return_value = mock_s

        # Webhook fires again (e.g. Stripe resend)
        self._post_webhook(
            "account.updated",
            {
                "id": "acct_already_ver",
                "charges_enabled": True,
                "payouts_enabled": True,
                "details_submitted": True,
            },
            mock_construct,
        )

        tip.refresh_from_db()
        # Tip must remain HELD since was_verified=True: no double transfer
        self.assertEqual(tip.status, TipStatus.HELD)
        mock_s.Transfer.create.assert_not_called()

    # ------------------------------------------------------------------
    # 6. Commission integrity
    # ------------------------------------------------------------------

    @patch("api.tip.services._stripe")
    @patch("api.tip.services.send_notification")
    def test_held_tip_commission_amounts_correct(self, mock_notify, mock_stripe):
        """
        For a $20 tip at 20% commission:
        - commission_amount = $4.00
        - recipient_amount  = $16.00
        - Transfer amount   = 1600 cents (platform keeps $4)
        """
        connect = make_connect_account(self.recipient, account_id="acct_comm_check")
        tip = self._make_held_tip(charge_id="ch_comm_check")

        mock_s = MagicMock()
        mock_stripe.return_value = mock_s
        mock_transfer = MagicMock()
        mock_transfer.id = "tr_comm"
        mock_s.Transfer.create.return_value = mock_transfer

        services.transfer_held_tip(tip, connect)

        call_kwargs = mock_s.Transfer.create.call_args.kwargs
        # Transfer only recipient_amount (commission stays on platform implicitly)
        self.assertEqual(call_kwargs["amount"], 1600)  # $16 in cents
        self.assertEqual(tip.commission_amount, Decimal("4.00"))
        self.assertEqual(tip.recipient_amount, Decimal("16.00"))

    # ------------------------------------------------------------------
    # 7. Serializer is_held flag
    # ------------------------------------------------------------------

    def test_is_held_true_for_held_status(self):
        from .serializers import TipSerializer
        tip = self._make_held_tip(charge_id="ch_flag")
        data = TipSerializer(tip).data
        self.assertTrue(data["is_held"])
        self.assertEqual(data["status"], "held")

    def test_is_held_false_for_succeeded_status(self):
        from .serializers import TipSerializer
        connect = make_connect_account(self.recipient)
        tip = make_tip(self.tipper, self.recipient, status=TipStatus.SUCCEEDED)
        data = TipSerializer(tip).data
        self.assertFalse(data["is_held"])

    def test_is_held_false_for_pending_status(self):
        from .serializers import TipSerializer
        tip = make_tip(self.tipper, self.recipient, status=TipStatus.PENDING)
        data = TipSerializer(tip).data
        self.assertFalse(data["is_held"])

    # ------------------------------------------------------------------
    # 8. History includes held tips
    # ------------------------------------------------------------------

    def test_held_tips_appear_in_history(self):
        """
        HELD tips must be visible in both sender and recipient histories.
        """
        self._make_held_tip()

        # Tipper view
        self.client.force_authenticate(user=self.tipper)
        response = self.client.get(reverse("tip:tip-history"), {"direction": "sent"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["status"], "held")

        # Recipient view
        self.client.force_authenticate(user=self.recipient)
        response = self.client.get(reverse("tip:tip-history"), {"direction": "received"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_filter_by_held_status(self):
        make_tip(self.tipper, self.recipient, status=TipStatus.SUCCEEDED)
        self._make_held_tip()
        self.client.force_authenticate(user=self.tipper)
        response = self.client.get(
            reverse("tip:tip-history"), {"direction": "sent", "status": "held"}
        )
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["status"], "held")
