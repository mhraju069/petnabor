"""
Tip System URL Configuration.
"""

from django.urls import path
from .views import (
    ConnectOnboardView,
    ConnectStatusView,
    ConnectDetailsView,
    ConnectDashboardLinkView,
    ConnectSwitchView,
    SendTipView,
    TipHistoryView,
    TipBalanceView,
    WithdrawView,
    WithdrawHistoryView,
    TipSettingsView,
    StripeWebhookView,
    OnboardBridgeView,
)

app_name = "tip"

urlpatterns = [
    # Stripe Connect onboarding
    path("connect/onboard/", ConnectOnboardView.as_view(), name="connect-onboard"),
    path("connect/status/", ConnectStatusView.as_view(), name="connect-status"),
    path("connect/details/", ConnectDetailsView.as_view(), name="connect-details"),
    path("connect/dashboard/", ConnectDashboardLinkView.as_view(), name="connect-dashboard"),
    path("connect/switch/", ConnectSwitchView.as_view(), name="connect-switch"),
    path("onboard/bridge/", OnboardBridgeView.as_view(), name="onboard-bridge"),

    # Tipping
    path("send/", SendTipView.as_view(), name="send-tip"),
    path("history/", TipHistoryView.as_view(), name="tip-history"),

    # Balance & Withdrawal
    path("balance/", TipBalanceView.as_view(), name="tip-balance"),
    path("withdraw/", WithdrawView.as_view(), name="withdraw"),
    path("withdraw/history/", WithdrawHistoryView.as_view(), name="withdraw-history"),

    # Settings (read-only for clients)
    path("settings/", TipSettingsView.as_view(), name="tip-settings"),

    # Stripe Webhook (no auth — verified by signature)
    path("webhook/", StripeWebhookView.as_view(), name="stripe-webhook"),
]
