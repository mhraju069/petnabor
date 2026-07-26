"""
Authentication and profile management views.

All business logic is delegated to the service layer in services.py.
"""

import logging

from rest_framework import status, serializers
from rest_framework.generics import RetrieveUpdateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, inline_serializer

from .throttles import (
    IdentityBasedOTPSendThrottle,
    IdentityBasedOTPVerifyThrottle,
    IdentityBasedLoginThrottle,
)

from .models import Profile, User
from .serializers import (
    ConfirmPasswordResetSerializer,
    FirebaseTokenSerializer,
    LoginSerializer,
    ProfileSerializer,
    RequestPasswordResetSerializer,
    ResendEmailOTPSerializer,
    ResendOTPSerializer,
    SignupSerializer,
    UserSerializer,
    VerifyEmailOTPSerializer,
    VerifyPhoneOTPSerializer,
)
from .services import (
    confirm_password_reset,
    firebase_login_service,
    login_user,
    request_password_reset,
    resend_email_otp,
    resend_phone_otp,
    signup_user,
    verify_email_otp,
    verify_phone_otp,
)

logger = logging.getLogger(__name__)


def _build_user_data(user):
    """Build a consistent user data dict for API responses."""
    return {
        "id": str(user.id),
        "email": user.email,
        "phone": user.phone,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "user_type": user.user_type,
        "is_verified": user.is_verified,
        "is_email_verified": user.is_email_verified,
        "is_phone_verified": user.is_phone_verified,
        "agree_to_terms_and_conditions": user.agree_to_terms_and_conditions,
        "ai_data_consent": user.ai_data_consent,
    }


class SignupView(APIView):
    """
    Register a new user with email or phone.
    Sends OTP via SMS (phone) or email.
    """

    permission_classes = [AllowAny]
    serializer_class = SignupSerializer
    throttle_classes = [IdentityBasedOTPSendThrottle]

    @extend_schema(
        request=SignupSerializer,
        responses={201: inline_serializer(
            name='SignupResponse',
            fields={
                'success': serializers.BooleanField(),
                'message': serializers.CharField(),
                'data': inline_serializer(
                    name='SignupData',
                    fields={
                        'user': serializers.DictField(),
                        'verification_type': serializers.CharField()
                    }
                )
            }
        )}
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        user, verification_type = signup_user(
            email=data.get("email"),
            phone=data.get("phone"),
            password=data.get("password"),
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            user_type=data.get("user_type", "petnabor"),
            agree_to_terms_and_conditions=data.get("agree_to_terms_and_conditions", False),
            ai_data_consent=data.get("ai_data_consent", False),
            referred_by_code=data.get("referred_by_code"),
        )

        messages = {
            "phone": "Account created. A verification OTP has been sent to your phone.",
            "email": "Account created. A verification OTP has been sent to your email.",
        }

        return Response(
            {
                "success": True,
                "message": messages.get(verification_type, "Account created successfully."),
                "data": {
                    "user": _build_user_data(user),
                    "verification_type": verification_type,
                },
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    """
    Authenticate with email/phone + password.
    Blocks unverified users.
    """

    permission_classes = [AllowAny]
    serializer_class = LoginSerializer
    throttle_classes = [IdentityBasedLoginThrottle]

    @extend_schema(
        request=LoginSerializer,
        responses={200: inline_serializer(
            name='LoginResponse',
            fields={
                'success': serializers.BooleanField(),
                'message': serializers.CharField(),
                'data': inline_serializer(
                    name='LoginData',
                    fields={
                        'access_token': serializers.CharField(),
                        'refresh_token': serializers.CharField(),
                        'user': serializers.DictField()
                    }
                )
            }
        )}
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        tokens, user = login_user(
            email_or_phone=serializer.validated_data["email_or_phone"],
            password=serializer.validated_data["password"],
        )

        return Response(
            {
                "success": True,
                "message": "Login successful.",
                "data": {
                    "access_token": tokens["access"],
                    "refresh_token": tokens["refresh"],
                    "user": _build_user_data(user),
                },
            },
            status=status.HTTP_200_OK,
        )


class VerifyPhoneOTPView(APIView):
    """Verify a 4-digit OTP code for phone verification."""

    permission_classes = [AllowAny]
    serializer_class = VerifyPhoneOTPSerializer
    throttle_classes = [IdentityBasedOTPVerifyThrottle]

    @extend_schema(
        request=VerifyPhoneOTPSerializer,
        responses={200: inline_serializer(
            name='VerifyPhoneResponse',
            fields={
                'success': serializers.BooleanField(),
                'message': serializers.CharField(),
                'data': inline_serializer(
                    name='VerifyPhoneData',
                    fields={'user': serializers.DictField()}
                )
            }
        )}
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone = serializer.validated_data["phone"]
        otp_code = serializer.validated_data["otp_code"]

        user = User.objects.filter(phone=phone).first()
        if not user:
            return Response(
                {"success": False, "message": "No account found with this phone number."},
                status=status.HTTP_404_NOT_FOUND,
            )

        verify_phone_otp(user, otp_code)

        return Response(
            {
                "success": True,
                "message": "Phone number verified successfully.",
                "data": {"user": _build_user_data(user)},
            },
            status=status.HTTP_200_OK,
        )


class VerifyEmailOTPView(APIView):
    """Verify a 4-digit OTP code for email verification."""

    permission_classes = [AllowAny]
    serializer_class = VerifyEmailOTPSerializer
    throttle_classes = [IdentityBasedOTPVerifyThrottle]

    @extend_schema(
        request=VerifyEmailOTPSerializer,
        responses={200: inline_serializer(
            name='VerifyEmailResponse',
            fields={
                'success': serializers.BooleanField(),
                'message': serializers.CharField(),
                'data': inline_serializer(
                    name='VerifyEmailData',
                    fields={'user': serializers.DictField()}
                )
            }
        )}
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        otp_code = serializer.validated_data["otp_code"]

        user = User.objects.filter(email=email.strip().lower()).first()
        if not user:
            return Response(
                {"success": False, "message": "No account found with this email."},
                status=status.HTTP_404_NOT_FOUND,
            )

        verify_email_otp(user, otp_code)

        return Response(
            {
                "success": True,
                "message": "Email verified successfully.",
                "data": {"user": _build_user_data(user)},
            },
            status=status.HTTP_200_OK,
        )


class ResendPhoneOTPView(APIView):
    """Resend OTP to a user's phone number."""

    permission_classes = [AllowAny]
    serializer_class = ResendOTPSerializer
    throttle_classes = [IdentityBasedOTPSendThrottle]

    @extend_schema(
        request=ResendOTPSerializer,
        responses={200: inline_serializer(
            name='ResendPhoneOTPResponse',
            fields={
                'success': serializers.BooleanField(),
                'message': serializers.CharField(),
            }
        )}
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        resend_phone_otp(serializer.validated_data["phone"])

        return Response(
            {
                "success": True,
                "message": "A new OTP has been sent to your phone.",
            },
            status=status.HTTP_200_OK,
        )


class ResendEmailOTPView(APIView):
    """Resend OTP to a user's email address."""

    permission_classes = [AllowAny]
    serializer_class = ResendEmailOTPSerializer
    throttle_classes = [IdentityBasedOTPSendThrottle]

    @extend_schema(
        request=ResendEmailOTPSerializer,
        responses={200: inline_serializer(
            name='ResendEmailOTPResponse',
            fields={
                'success': serializers.BooleanField(),
                'message': serializers.CharField(),
            }
        )}
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        resend_email_otp(serializer.validated_data["email"])

        return Response(
            {
                "success": True,
                "message": "A new OTP has been sent to your email.",
            },
            status=status.HTTP_200_OK,
        )


class RequestPasswordResetView(APIView):
    """Send a password reset OTP to user's email or phone."""

    permission_classes = [AllowAny]
    serializer_class = RequestPasswordResetSerializer
    throttle_classes = [IdentityBasedOTPSendThrottle]

    @extend_schema(
        request=RequestPasswordResetSerializer,
        responses={200: inline_serializer(
            name='RequestPasswordResetResponse',
            fields={
                'success': serializers.BooleanField(),
                'message': serializers.CharField(),
                'data': inline_serializer(
                    name='PasswordResetDeliveryData',
                    fields={'delivery_method': serializers.CharField()}
                )
            }
        )}
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        delivery_method = request_password_reset(
            serializer.validated_data["email_or_phone"]
        )

        messages = {
            "email": "A password reset OTP has been sent to your email.",
            "phone": "A password reset OTP has been sent to your phone.",
        }

        return Response(
            {
                "success": True,
                "message": messages.get(delivery_method, "OTP sent."),
                "data": {"delivery_method": delivery_method},
            },
            status=status.HTTP_200_OK,
        )


class ConfirmPasswordResetView(APIView):
    """Verify OTP and set a new password."""

    permission_classes = [AllowAny]
    serializer_class = ConfirmPasswordResetSerializer
    throttle_classes = [IdentityBasedOTPVerifyThrottle]

    @extend_schema(
        request=ConfirmPasswordResetSerializer,
        responses={200: inline_serializer(
            name='ConfirmPasswordResetResponse',
            fields={
                'success': serializers.BooleanField(),
                'message': serializers.CharField(),
            }
        )}
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        confirm_password_reset(
            email_or_phone=serializer.validated_data["email_or_phone"],
            otp_code=serializer.validated_data["otp_code"],
            new_password=serializer.validated_data["new_password"],
        )

        return Response(
            {
                "success": True,
                "message": "Password has been reset successfully. You can now log in.",
            },
            status=status.HTTP_200_OK,
        )


class FirebaseLoginView(APIView):
    """
    Login/Register via Firebase ID token.
    Firebase users are auto-verified.
    """

    permission_classes = [AllowAny]
    serializer_class = FirebaseTokenSerializer

    @extend_schema(
        request=FirebaseTokenSerializer,
        responses={200: inline_serializer(
            name='FirebaseLoginResponse',
            fields={
                'success': serializers.BooleanField(),
                'message': serializers.CharField(),
                'data': inline_serializer(
                    name='FirebaseLoginData',
                    fields={
                        'access_token': serializers.CharField(),
                        'refresh_token': serializers.CharField(),
                        'user': serializers.DictField()
                    }
                )
            }
        )}
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        tokens, user , is_new_user= firebase_login_service(
            id_token=serializer.validated_data["id_token"],
            first_name=serializer.validated_data.get("first_name", ""),
            last_name=serializer.validated_data.get("last_name", ""),
            user_type=serializer.validated_data.get("user_type", "petnabor"),
            agree_to_terms_and_conditions=serializer.validated_data.get(
                "agree_to_terms_and_conditions", False
            ),
            ai_data_consent=serializer.validated_data.get(
                "ai_data_consent", False
            ),
            referred_by_code=serializer.validated_data.get("referred_by_code"),
            nonce=serializer.validated_data.get("nonce", ""),
        )

        return Response(
            {
                "success": True,
                "message": "Login successful.",
                "data": {
                    "access_token": tokens["access"],
                    "refresh_token": tokens["refresh"],
                    "user": _build_user_data(user),
                },
                "is_new_user": is_new_user,
            },
            status=status.HTTP_200_OK,
        )


class UserDetailView(RetrieveUpdateDestroyAPIView):
    """Current user's details (GET/PUT/PATCH/DELETE)."""

    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


class ProfileDetailView(RetrieveUpdateAPIView):
    """GET/PUT/PATCH /api/users/profile/ — Current user's profile."""

    serializer_class = ProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        from api.users.signals import _unique_referral_code
        profile, _ = Profile.objects.select_related("user").get_or_create(
            user=self.request.user
        )
        # Backfill referral code for existing profiles that don't have one
        if not profile.referral_code:
            profile.referral_code = _unique_referral_code()
            profile.save(update_fields=["referral_code"])
        return profile

    def perform_update(self, serializer):
        profile = serializer.save()

        from django.db import transaction
        from .tasks import process_profile_media_task

        if "profile_picture" in self.request.FILES:
            transaction.on_commit(
                lambda: process_profile_media_task.delay(str(profile.id), "profile_picture")
            )

        if "cover_photo" in self.request.FILES:
            transaction.on_commit(
                lambda: process_profile_media_task.delay(str(profile.id), "cover_photo")
            )
