"""
Tip System Views.
"""

import logging
import json

import stripe
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.conf import settings
from rest_framework import status, generics
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, OpenApiResponse

from api.users.models import User
from api.meeting.models import Meeting
from .models import TipSettings, Tip, TipWithdrawal, TipStatus
from .serializers import (
    TipSettingsSerializer,
    StripeConnectAccountSerializer,
    SendTipSerializer,
    TipSerializer,
    WithdrawSerializer,
    TipWithdrawalSerializer,
)
from . import services
from .paginations import StandardResultsSetPagination

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Stripe Connect — Onboarding
# ──────────────────────────────────────────────────────────────────────────────


class ConnectOnboardView(APIView):
    """
    POST /tip/connect/onboard/
    Create (or refresh) a Stripe Express onboarding link for the current user.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get Stripe Connect onboarding URL",
        responses={
            200: OpenApiResponse(description="Returns onboarding URL and account status."),
        },
        tags=["Tip / Stripe Connect"],
    )
    def post(self, request):
        try:
            connect, onboarding_url = services.create_connect_account(request.user)
        except stripe.error.StripeError as exc:
            logger.error("Stripe error during Connect onboarding: %s", str(exc))
            return Response(
                {"detail": "Stripe service unavailable. Please try again later."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        serializer = StripeConnectAccountSerializer(connect)
        return Response(
            {
                "account": serializer.data,
                "onboarding_url": onboarding_url,
            },
            status=status.HTTP_200_OK,
        )


class ConnectStatusView(APIView):
    """
    GET /tip/connect/status/
    Fetch and sync the current user's Stripe Connect account status.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get Stripe Connect account status",
        responses={
            200: OpenApiResponse(description="Current connect account status, or {'is_connect': False} if not connected."),
        },
        tags=["Tip / Stripe Connect"],
    )
    def get(self, request):
        try:
            connect = services.get_connect_account_status(request.user)
        except stripe.error.StripeError as exc:
            logger.error("Stripe error fetching account status: %s", str(exc))
            return Response(
                {"detail": "Failed to fetch status from Stripe."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        if connect is None:
            return Response(
                {"is_connect": False},
                status=status.HTTP_200_OK,
            )

        serializer = StripeConnectAccountSerializer(connect)
        return Response(serializer.data, status=status.HTTP_200_OK)


class OnboardBridgeView(APIView):
    """
    GET /tip/onboard/bridge/?status=return|refresh
    A simple HTTPS bridge that redirects the browser to the mobile app's deep link.
    This is required because Stripe does not allow direct deep links as redirect URLs.
    """
    permission_classes = [AllowAny]

    @extend_schema(exclude=True)
    def get(self, request):
        status_param = request.query_params.get("status", "return")
        frontend_base = getattr(settings, "FRONTEND_BASE_URL", "petnabor://")

        # Construct the deep link (e.g. petnabor://tip/onboard/return)
        deep_link = f"{frontend_base}tip/onboard/{status_param}"

        # Simple HTML redirect template
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Redirecting to App...</title>
            <meta http-equiv="refresh" content="0; url={deep_link}">
            <script type="text/javascript">
                window.location.href = "{deep_link}";
            </script>
        </head>
        <body>
            <div style="text-align: center; margin-top: 50px; font-family: sans-serif;">
                <h2>Onboarding {status_param.capitalize()}</h2>
                <p>If you are not redirected automatically, <a href="{deep_link}">click here to return to the app</a>.</p>
            </div>
        </body>
        </html>
        """
        from django.http import HttpResponse
        return HttpResponse(html_content)


# ──────────────────────────────────────────────────────────────────────────────
# Send Tip
# ──────────────────────────────────────────────────────────────────────────────


class SendTipView(APIView):
    """
    POST /tip/send/
    Create a PaymentIntent for a tip. Returns client_secret for mobile SDK.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Send a tip (creates PaymentIntent)",
        request=SendTipSerializer,
        responses={
            201: OpenApiResponse(description="PaymentIntent created, client_secret returned."),
            400: OpenApiResponse(description="Validation error."),
            404: OpenApiResponse(description="Recipient or meeting not found."),
        },
        tags=["Tip"],
    )
    def post(self, request):
        serializer = SendTipSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        recipient_id = data["recipient_id"]
        meeting_id = data.get("meeting_id")
        amount = data["amount"]
        note = data.get("note", "")
        idempotency_key = data.get("idempotency_key")

        # Self-tip guard
        if str(request.user.id) == str(recipient_id):
            return Response(
                {"detail": "You cannot tip yourself."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Resolve recipient
        try:
            recipient = User.objects.get(id=recipient_id)
        except User.DoesNotExist:
            return Response(
                {"detail": "Recipient not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Resolve optional meeting
        meeting = None
        if meeting_id:
            try:
                meeting = Meeting.objects.get(id=meeting_id)
                # Verify both parties are in the meeting
                if request.user not in (meeting.sender, meeting.receiver):
                    return Response(
                        {"detail": "You are not a participant in this meeting."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
                if recipient not in (meeting.sender, meeting.receiver):
                    return Response(
                        {"detail": "Recipient is not a participant in this meeting."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            except Meeting.DoesNotExist:
                return Response(
                    {"detail": "Meeting not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

        # Create PaymentIntent via service
        try:
            tip, client_secret = services.create_tip_payment_intent(
                tipper=request.user,
                recipient=recipient,
                amount=amount,
                meeting=meeting,
                note=note,
                idempotency_key=str(idempotency_key) if idempotency_key else None,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except stripe.error.StripeError as exc:
            logger.error("Stripe error creating PaymentIntent: %s", str(exc))
            return Response(
                {"detail": "Stripe service error. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        tip_serializer = TipSerializer(tip)
        return Response(
            {
                "tip": tip_serializer.data,
                "client_secret": client_secret,
                "publishable_key": settings.STRIPE_PUBLISHABLE_KEY,
            },
            status=status.HTTP_201_CREATED,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Tip History
# ──────────────────────────────────────────────────────────────────────────────


class TipHistoryView(generics.ListAPIView):
    """
    GET /tip/history/?direction=sent|received
    Returns tip history for the current user with optional direction filter.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = TipSerializer
    pagination_class = StandardResultsSetPagination

    @extend_schema(
        summary="Get tip history",
        responses={200: TipSerializer(many=True)},
        tags=["Tip"],
    )
    def get_queryset(self):
        direction = self.request.query_params.get("direction", "all")  # sent | received | all

        # Statuses that a recipient should see:
        # Only confirmed/settled payments are relevant to the receiver.
        # PENDING = sender hasn't completed card payment yet (sender-side only)
        # FAILED / CANCELLED = sender-side failures, irrelevant to receiver
        RECEIVER_VISIBLE_STATUSES = [
            TipStatus.SUCCEEDED,
            TipStatus.HELD,
            TipStatus.REFUNDED,
        ]

        if direction == "sent":
            # Sender sees everything: PENDING, FAILED, CANCELLED, SUCCEEDED, etc.
            qs = Tip.objects.select_related("tipper", "recipient", "meeting").filter(
                tipper=self.request.user
            )
        elif direction == "received":
            # Receiver sees only confirmed payments
            qs = Tip.objects.select_related("tipper", "recipient", "meeting").filter(
                recipient=self.request.user,
                status__in=RECEIVER_VISIBLE_STATUSES,
            )
        else:
            # "all" view: sent tips (all statuses) + received tips (confirmed only)
            sent_qs = Tip.objects.select_related("tipper", "recipient", "meeting").filter(
                tipper=self.request.user
            )
            received_qs = Tip.objects.select_related("tipper", "recipient", "meeting").filter(
                recipient=self.request.user,
                status__in=RECEIVER_VISIBLE_STATUSES,
            )
            qs = (sent_qs | received_qs).order_by("-created_at")

        # Allow explicit status filter on top (useful for "show only my failed tips" etc.)
        status_filter = self.request.query_params.get("status")
        if status_filter and status_filter in TipStatus.values:
            qs = qs.filter(status=status_filter)

        return qs


# ──────────────────────────────────────────────────────────────────────────────
# Balance
# ──────────────────────────────────────────────────────────────────────────────


class TipBalanceView(APIView):
    """
    GET /tip/balance/
    Returns a full balance breakdown for the current user.

    Fields:
    - available_balance:  Immediately withdrawable (Stripe settled balance)
    - pending_balance:    Stripe is still settling (2-7 days, not withdrawable yet)
    - held_amount:        Confirmed tips waiting for you to connect your bank account
    - incoming_amount:    Tips where payment hasn't been confirmed by Stripe yet
    - total_earned:       Lifetime earnings (all succeeded tips)
    - is_connected:       Whether you have a verified Stripe account
    - is_fully_verified:  Whether your Stripe account is fully verified
    - minimum_withdrawal: Minimum amount you can withdraw
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get full tip balance summary",
        tags=["Tip"],
    )
    def get(self, request):
        try:
            summary = services.get_full_balance_summary(request.user)
        except Exception as exc:
            logger.error("Balance summary error for user %s: %s", request.user.id, exc)
            return Response(
                {"detail": "Failed to fetch balance. Please try again later."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        tip_settings = TipSettings.get_instance()

        # Build human-readable notes so the app can display helpful messages
        notes = []
        if summary["held_amount"] > 0:
            notes.append(
                f"${summary['held_amount']:.2f} is held and will be released automatically "
                "once you connect your bank account."
            )
        if summary["pending_balance"] > 0:
            notes.append(
                f"${summary['pending_balance']:.2f} is pending settlement by Stripe "
                "(typically 2-7 business days) and is not yet withdrawable."
            )
        if not summary["is_connected"]:
            notes.append(
                "Connect your bank account to receive tips and make withdrawals."
            )
        elif not summary["is_fully_verified"]:
            notes.append(
                "Complete your Stripe onboarding to unlock withdrawals."
            )

        return Response(
            {
                "available_balance": f"{summary['available_balance']:.2f}",
                "pending_balance": f"{summary['pending_balance']:.2f}",
                "held_amount": f"{summary['held_amount']:.2f}",
                "total_earned": f"{summary['total_earned']:.2f}",
                "currency": summary["currency"],
                "is_connected": summary["is_connected"],
                "is_fully_verified": summary["is_fully_verified"],
                "minimum_withdrawal": str(tip_settings.minimum_withdrawal_amount),
                "can_withdraw": (
                    summary["is_fully_verified"]
                    and summary["available_balance"] >= tip_settings.minimum_withdrawal_amount
                ),
                "notes": notes,
            },
            status=status.HTTP_200_OK,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Withdrawal
# ──────────────────────────────────────────────────────────────────────────────


class WithdrawView(APIView):
    """
    POST /tip/withdraw/
    Request a payout of available tip earnings.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Withdraw tip earnings",
        request=WithdrawSerializer,
        responses={
            201: TipWithdrawalSerializer,
            400: OpenApiResponse(description="Validation or business logic error."),
        },
        tags=["Tip"],
    )
    def post(self, request):
        serializer = WithdrawSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        amount = serializer.validated_data["amount"]

        try:
            withdrawal = services.create_withdrawal(request.user, amount)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except stripe.error.StripeError as exc:
            logger.error("Stripe payout error: %s", str(exc))
            return Response(
                {"detail": "Stripe payout failed. Please try again later."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(
            TipWithdrawalSerializer(withdrawal).data,
            status=status.HTTP_201_CREATED,
        )


class WithdrawHistoryView(generics.ListAPIView):
    """
    GET /tip/withdraw/history/
    Returns withdrawal history for the current user.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = TipWithdrawalSerializer
    pagination_class = StandardResultsSetPagination

    @extend_schema(
        summary="Get withdrawal history",
        responses={200: TipWithdrawalSerializer(many=True)},
        tags=["Tip"],
    )
    def get_queryset(self):
        return TipWithdrawal.objects.filter(user=self.request.user).order_by("-created_at")


# ──────────────────────────────────────────────────────────────────────────────
# Tip Settings (public read)
# ──────────────────────────────────────────────────────────────────────────────


class TipSettingsView(APIView):
    """
    GET /tip/settings/
    Returns current tip platform settings (commission %, min/max amounts).
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get tip platform settings",
        responses={200: TipSettingsSerializer},
        tags=["Tip"],
    )
    def get(self, request):
        tip_settings = TipSettings.get_instance()
        return Response(TipSettingsSerializer(tip_settings).data)


# ──────────────────────────────────────────────────────────────────────────────
# Stripe Webhook
# ──────────────────────────────────────────────────────────────────────────────


@method_decorator(csrf_exempt, name="dispatch")
class StripeWebhookView(APIView):
    """
    POST /tip/webhook/
    Receives and verifies Stripe webhook events.
    CSRF-exempt — authentication is via Stripe signature header.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(exclude=True)
    def post(self, request):
        payload = request.body
        sig_header = request.META.get("HTTP_STRIPE_SIGNATURE")

        if not sig_header:
            return Response(
                {"detail": "Missing Stripe-Signature header."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            event = services.construct_stripe_event(payload, sig_header)
        except stripe.error.SignatureVerificationError as exc:
            logger.warning("Stripe webhook signature verification failed: %s", str(exc))
            return Response(
                {"detail": "Invalid signature."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            logger.error("Stripe webhook parse error: %s", str(exc))
            return Response(
                {"detail": "Webhook parse error."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        event_type = event["type"]
        event_data = event["data"]["object"]

        logger.info("Stripe webhook received: %s (id=%s)", event_type, event.get("id"))

        HANDLERS = {
            "payment_intent.succeeded": services.handle_payment_intent_succeeded,
            "payment_intent.payment_failed": services.handle_payment_intent_failed,
            "payment_intent.canceled": services.handle_payment_intent_cancelled,
            "charge.refunded": services.handle_charge_refunded,
            "account.updated": services.handle_account_updated,
            "payout.paid": services.handle_payout_paid,
            "payout.failed": services.handle_payout_failed,
        }

        handler = HANDLERS.get(event_type)
        if handler:
            try:
                handler(event_data)
            except Exception as exc:
                logger.exception(
                    "Error handling webhook %s (event_id=%s): %s",
                    event_type,
                    event.get("id"),
                    str(exc),
                )
                # Return 200 to prevent Stripe from retrying (we log and fix manually)
                return Response({"detail": "Handler error logged."}, status=status.HTTP_200_OK)
        else:
            logger.debug("Unhandled Stripe event type: %s", event_type)

        return Response({"received": True}, status=status.HTTP_200_OK)
