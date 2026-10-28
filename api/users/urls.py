"""
URL patterns for the users and authentication API.
"""
from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    ConfirmPasswordResetView,
    FirebaseLoginView,
    LoginView,
    ProfileDetailView,
    RequestPasswordResetView,
    ResendEmailOTPView,
    ResendPhoneOTPView,
    SignupView,
    UserDetailView,
    DeactivateAccountView,
    VerifyEmailOTPView,
    VerifyPhoneOTPView,
)

urlpatterns = [
    path("signup/", SignupView.as_view(), name="signup"),
    path("login/", LoginView.as_view(), name="login"),
    path("login/firebase/", FirebaseLoginView.as_view(), name="firebase-login"),
    path("verify-phone/", VerifyPhoneOTPView.as_view(), name="verify-phone"),
    path("verify-email/", VerifyEmailOTPView.as_view(), name="verify-email"),
    path("resend-phone-otp/", ResendPhoneOTPView.as_view(), name="resend-phone-otp"),
    path("resend-email-otp/", ResendEmailOTPView.as_view(), name="resend-email-otp"),
    path("password-reset/request/", RequestPasswordResetView.as_view(), name="password-reset-request"),
    path("password-reset/confirm/", ConfirmPasswordResetView.as_view(), name="password-reset-confirm"),
    path("user/", UserDetailView.as_view(), name="user-detail"),
    path("user/deactivate/", DeactivateAccountView.as_view(), name="user-deactivate"),
    path("profile/", ProfileDetailView.as_view(), name="profile-detail"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
]
