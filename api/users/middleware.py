from django.conf import settings
"""
Authentication and activity tracking middleware.
"""

import logging

from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)

# Endpoints that unverified users can access
AUTH_WHITELISTED_PATHS = (
    "/api/users/signup/",
    "/api/users/login/",
    "/api/users/login/firebase/",
    "/api/users/verify-phone/",
    "/api/users/verify-email/",
    "/api/users/resend-phone-otp/",
    "/api/users/resend-email-otp/",
    "/api/users/password-reset/",
    "/api/users/token/refresh/",
    "/" + settings.ADMIN_URL,  # wherever DJANGO_ADMIN_URL puts the admin
)

# Throttle last_active DB writes (seconds)
LAST_ACTIVE_THROTTLE_S = 60


class UpdateLastActiveMiddleware(MiddlewareMixin):
    """
    Update the authenticated user's last_active timestamp on every request.
    Throttled per user to one DB write per LAST_ACTIVE_THROTTLE_S.
    """

    def process_request(self, request):
        if not request.user.is_authenticated:
            try:
                header = JWTAuthentication().get_header(request)
                if header:
                    raw_token = JWTAuthentication().get_raw_token(header)
                    validated_token = JWTAuthentication().get_validated_token(raw_token)
                    user = JWTAuthentication().get_user(validated_token)
                    request.user = user
            except Exception:
                pass

        if request.user.is_authenticated:
            cache_key = f"last_active_ts_{request.user.id}"
            if not cache.get(cache_key):
                User = get_user_model()
                User.objects.filter(id=request.user.id).update(
                    last_active=timezone.now()
                )
                cache.set(cache_key, True, LAST_ACTIVE_THROTTLE_S)

        return None


class VerificationEnforcementMiddleware(MiddlewareMixin):
    """
    Block unverified users from accessing protected API endpoints.
    Allows whitelisted paths for verification completion.
    """

    def process_request(self, request):
        path = request.path
        if any(path.startswith(whitelisted) for whitelisted in AUTH_WHITELISTED_PATHS):
            return None

        if not request.user.is_authenticated:
            return None

        if not request.user.is_verified:
            return JsonResponse(
                {
                    "success": False,
                    "message": "Your account is not verified. Please verify your email or phone number.",
                    "error_code": "account_not_verified",
                },
                status=403,
            )

        return None