"""
Comprehensive test suite for the authentication system.

Tests cover: signup, login, phone OTP, email OTP, password reset,
resend flows, middleware enforcement, and edge cases.

All external services (Twilio, SMTP) are mocked.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from .models import OTPTypes, OTPVerification
from .services import _hash_otp, get_tokens_for_user

User = get_user_model()



# ──────────────────────────────────────────────
# Test Configuration
# ──────────────────────────────────────────────

TEST_PASSWORD = "TestPass1"
TEST_PHONE = "+1234567890"
TEST_EMAIL = "test@example.com"


def _create_verified_user(email=TEST_EMAIL, phone=None, password=TEST_PASSWORD):
    """Helper to create a verified user for tests."""
    user = User.objects.create_user(
        email=email,
        phone=phone,
        password=password,
        username=f"test_{uuid.uuid4().hex[:8]}",
        is_verified=True,
        is_email_verified=bool(email),
        is_phone_verified=bool(phone),
    )
    return user


def _create_unverified_user(email=TEST_EMAIL, phone=None, password=TEST_PASSWORD):
    """Helper to create an unverified user for tests."""
    user = User.objects.create_user(
        email=email,
        phone=phone,
        password=password,
        username=f"test_{uuid.uuid4().hex[:8]}",
        is_verified=False,
        is_email_verified=False,
        is_phone_verified=False,
    )
    return user


# ──────────────────────────────────────────────
# Signup Tests
# ──────────────────────────────────────────────


@override_settings(OTP_LENGTH=4, OTP_EXPIRY_MINUTES=5, OTP_MAX_ATTEMPTS=5)
@patch("rest_framework.views.APIView.check_throttles", lambda self, request: None)
class SignupTests(APITestCase):
    """Test user registration with email and phone."""

    def setUp(self):
        self.client = APIClient()
        self.signup_url = reverse("signup")

    @patch("api.users.services.send_email_otp")
    def test_signup_with_email(self, mock_send_email):
        """Signup with email sends OTP email."""
        response = self.client.post(self.signup_url, {
            "email": "newuser@example.com",
            "password": TEST_PASSWORD,
            "first_name": "John",
            "last_name": "Doe",
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["verification_type"], "email")
        self.assertFalse(response.data["data"]["user"]["is_verified"])
        mock_send_email.assert_called_once()

    @patch("api.users.services.send_phone_otp")
    def test_signup_with_phone(self, mock_send_otp):
        """Signup with phone sends OTP."""
        response = self.client.post(self.signup_url, {
            "phone": TEST_PHONE,
            "password": TEST_PASSWORD,
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["verification_type"], "phone")
        mock_send_otp.assert_called_once()

    def test_signup_missing_email_and_phone(self):
        """Signup requires at least email or phone."""
        response = self.client.post(self.signup_url, {"password": TEST_PASSWORD})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("api.users.services.send_email_otp")
    def test_signup_duplicate_email(self, mock_send_email):
        """Signup with existing email returns 409."""
        _create_verified_user(email="existing@example.com")
        response = self.client.post(self.signup_url, {
            "email": "existing@example.com",
            "password": TEST_PASSWORD,
        })
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    @patch("api.users.services.send_phone_otp")
    def test_signup_duplicate_phone(self, mock_send_otp):
        """Signup with existing phone returns 409."""
        _create_verified_user(email=None, phone="+9876543210")
        response = self.client.post(self.signup_url, {
            "phone": "+9876543210",
            "password": TEST_PASSWORD,
        })
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_signup_weak_password(self):
        """Signup with weak password returns validation error."""
        response = self.client.post(self.signup_url, {
            "email": "weak@example.com",
            "password": "123",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_signup_invalid_phone_format(self):
        """Signup with invalid phone format returns validation error."""
        response = self.client.post(self.signup_url, {
            "phone": "not-a-phone",
            "password": TEST_PASSWORD,
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("api.users.services.send_email_otp")
    def test_signup_with_ai_data_consent(self, mock_send_email):
        """Signup with ai_data_consent should save it correctly."""
        response = self.client.post(self.signup_url, {
            "email": "ai_consent@example.com",
            "password": TEST_PASSWORD,
            "ai_data_consent": True,
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["data"]["user"]["ai_data_consent"])

        user = User.objects.get(email="ai_consent@example.com")
        self.assertTrue(user.ai_data_consent)


# ──────────────────────────────────────────────
# Phone OTP Verification Tests
# ──────────────────────────────────────────────


@override_settings(OTP_LENGTH=4, OTP_EXPIRY_MINUTES=5, OTP_MAX_ATTEMPTS=5)
@patch("rest_framework.views.APIView.check_throttles", lambda self, request: None)
class PhoneOTPTests(APITestCase):
    """Test phone OTP verification flow."""

    def setUp(self):
        self.client = APIClient()
        self.verify_url = reverse("verify-phone")
        self.user = _create_unverified_user(email=None, phone=TEST_PHONE)

    def _create_otp(self, otp_code="1234", expired=False):
        offset = -1 if expired else 5
        OTPVerification.objects.create(
            user=self.user,
            otp_hash=_hash_otp(otp_code),
            otp_type=OTPTypes.PHONE_SIGNUP,
            expires_at=timezone.now() + timedelta(minutes=offset),
        )
        return otp_code

    def test_verify_valid_otp(self):
        """Correct OTP verifies the user."""
        otp = self._create_otp("5678")
        response = self.client.post(self.verify_url, {"phone": TEST_PHONE, "otp_code": otp})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_verified)
        self.assertTrue(self.user.is_phone_verified)

    def test_verify_wrong_otp(self):
        """Wrong OTP returns error."""
        self._create_otp("1234")
        response = self.client.post(self.verify_url, {"phone": TEST_PHONE, "otp_code": "9999"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_verify_expired_otp(self):
        """Expired OTP returns error."""
        self._create_otp("1234", expired=True)
        response = self.client.post(self.verify_url, {"phone": TEST_PHONE, "otp_code": "1234"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_verify_max_attempts(self):
        """Too many wrong attempts locks out the OTP."""
        OTPVerification.objects.create(
            user=self.user,
            otp_hash=_hash_otp("1234"),
            otp_type=OTPTypes.PHONE_SIGNUP,
            expires_at=timezone.now() + timedelta(minutes=5),
            attempt_count=5,
        )
        response = self.client.post(self.verify_url, {"phone": TEST_PHONE, "otp_code": "1234"})
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_verify_no_active_otp(self):
        """No active OTP returns error."""
        response = self.client.post(self.verify_url, {"phone": TEST_PHONE, "otp_code": "1234"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_verify_nonexistent_phone(self):
        """Non-existent phone returns 404."""
        response = self.client.post(self.verify_url, {"phone": "+9999999999", "otp_code": "1234"})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ──────────────────────────────────────────────
# Email OTP Verification Tests
# ──────────────────────────────────────────────


@override_settings(OTP_LENGTH=4, OTP_EXPIRY_MINUTES=5, OTP_MAX_ATTEMPTS=5)
@patch("rest_framework.views.APIView.check_throttles", lambda self, request: None)
class EmailOTPTests(APITestCase):
    """Test email OTP verification flow."""

    def setUp(self):
        self.client = APIClient()
        self.verify_url = reverse("verify-email")
        self.user = _create_unverified_user(email=TEST_EMAIL)

    def _create_email_otp(self, otp_code="1234", expired=False):
        offset = -1 if expired else 5
        OTPVerification.objects.create(
            user=self.user,
            otp_hash=_hash_otp(otp_code),
            otp_type=OTPTypes.EMAIL_SIGNUP,
            expires_at=timezone.now() + timedelta(minutes=offset),
        )
        return otp_code

    def test_verify_valid_email_otp(self):
        """Correct email OTP verifies the user."""
        otp = self._create_email_otp("4567")
        response = self.client.post(self.verify_url, {"email": TEST_EMAIL, "otp_code": otp})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_verified)
        self.assertTrue(self.user.is_email_verified)

    def test_verify_wrong_email_otp(self):
        """Wrong email OTP returns error."""
        self._create_email_otp("1234")
        response = self.client.post(self.verify_url, {"email": TEST_EMAIL, "otp_code": "9999"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_verify_expired_email_otp(self):
        """Expired email OTP returns error."""
        self._create_email_otp("1234", expired=True)
        response = self.client.post(self.verify_url, {"email": TEST_EMAIL, "otp_code": "1234"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_verify_nonexistent_email(self):
        """Non-existent email returns 404."""
        response = self.client.post(self.verify_url, {"email": "noone@example.com", "otp_code": "1234"})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ──────────────────────────────────────────────
# Login Tests
# ──────────────────────────────────────────────


@patch("rest_framework.views.APIView.check_throttles", lambda self, request: None)
class LoginTests(APITestCase):
    """Test login with email/phone + password."""

    def setUp(self):
        self.client = APIClient()
        self.login_url = reverse("login")

    def test_login_with_email(self):
        """Verified user can login with email."""
        _create_verified_user(email="login@example.com")
        response = self.client.post(self.login_url, {
            "email_or_phone": "login@example.com",
            "password": TEST_PASSWORD,
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access_token", response.data["data"])

    def test_login_with_phone(self):
        """Verified user can login with phone."""
        _create_verified_user(email=None, phone="+1122334455")
        response = self.client.post(self.login_url, {
            "email_or_phone": "+1122334455",
            "password": TEST_PASSWORD,
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_login_wrong_password(self):
        _create_verified_user(email="wrong@example.com")
        response = self.client.post(self.login_url, {
            "email_or_phone": "wrong@example.com",
            "password": "WrongPass1",
        })
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_nonexistent_user(self):
        response = self.client.post(self.login_url, {
            "email_or_phone": "noone@example.com",
            "password": TEST_PASSWORD,
        })
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_unverified_user(self):
        """Unverified user is blocked from login."""
        _create_unverified_user(email="unverified@example.com")
        response = self.client.post(self.login_url, {
            "email_or_phone": "unverified@example.com",
            "password": TEST_PASSWORD,
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_login_missing_credentials(self):
        response = self.client.post(self.login_url, {})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ──────────────────────────────────────────────
# Password Reset Tests
# ──────────────────────────────────────────────


@override_settings(OTP_LENGTH=4, OTP_EXPIRY_MINUTES=5, OTP_MAX_ATTEMPTS=5)
@patch("rest_framework.views.APIView.check_throttles", lambda self, request: None)
class PasswordResetTests(APITestCase):
    """Test password reset flow."""

    def setUp(self):
        self.client = APIClient()
        self.request_url = reverse("password-reset-request")
        self.confirm_url = reverse("password-reset-confirm")

    @patch("api.users.services._send_password_reset_email")
    def test_request_reset_via_email(self, mock_send):
        """Password reset sends OTP to email."""
        _create_verified_user(email="reset@example.com")
        response = self.client.post(self.request_url, {"email_or_phone": "reset@example.com"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["delivery_method"], "email")
        mock_send.assert_called_once()

    @patch("api.users.services.send_phone_otp")
    def test_request_reset_via_phone(self, mock_send):
        """Password reset sends OTP to phone."""
        _create_verified_user(email=None, phone="+5551234567")
        response = self.client.post(self.request_url, {"email_or_phone": "+5551234567"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["delivery_method"], "phone")
        mock_send.assert_called_once()

    def test_request_reset_nonexistent(self):
        """Reset request for non-existent user returns error."""
        response = self.client.post(self.request_url, {"email_or_phone": "nobody@example.com"})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch("api.users.services._send_password_reset_email")
    def test_confirm_reset_success(self, mock_send):
        """Valid OTP resets the password."""
        user = _create_verified_user(email="confirm@example.com")
        otp = "1234"
        OTPVerification.objects.create(
            user=user,
            otp_hash=_hash_otp(otp),
            otp_type=OTPTypes.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        new_password = "NewSecure1"
        response = self.client.post(self.confirm_url, {
            "email_or_phone": "confirm@example.com",
            "otp_code": otp,
            "new_password": new_password,
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify new password works
        user.refresh_from_db()
        self.assertTrue(user.check_password(new_password))

    def test_confirm_reset_wrong_otp(self):
        """Wrong OTP during password reset returns error."""
        user = _create_verified_user(email="wrongotp@example.com")
        OTPVerification.objects.create(
            user=user,
            otp_hash=_hash_otp("1234"),
            otp_type=OTPTypes.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        response = self.client.post(self.confirm_url, {
            "email_or_phone": "wrongotp@example.com",
            "otp_code": "9999",
            "new_password": "NewSecure1",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ──────────────────────────────────────────────
# Resend Tests
# ──────────────────────────────────────────────


@patch("rest_framework.views.APIView.check_throttles", lambda self, request: None)
class ResendTests(APITestCase):
    """Test OTP resend flows."""

    def setUp(self):
        self.client = APIClient()

    @patch("api.users.services.send_phone_otp")
    def test_resend_phone_otp(self, mock_send_otp):
        _create_unverified_user(email=None, phone=TEST_PHONE)
        response = self.client.post(reverse("resend-phone-otp"), {"phone": TEST_PHONE})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_send_otp.assert_called_once()

    def test_resend_phone_nonexistent(self):
        response = self.client.post(reverse("resend-phone-otp"), {"phone": "+9999999999"})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch("api.users.services.send_phone_otp")
    def test_resend_phone_already_verified(self, mock_send_otp):
        user = _create_verified_user(email=None, phone="+5555555555")
        user.is_phone_verified = True
        user.save(update_fields=["is_phone_verified"])
        response = self.client.post(reverse("resend-phone-otp"), {"phone": "+5555555555"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("api.users.services.send_email_otp")
    def test_resend_email_otp(self, mock_send_email):
        _create_unverified_user(email="resend@example.com")
        response = self.client.post(reverse("resend-email-otp"), {"email": "resend@example.com"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_send_email.assert_called_once()

    def test_resend_email_nonexistent(self):
        response = self.client.post(reverse("resend-email-otp"), {"email": "nobody@example.com"})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# ──────────────────────────────────────────────
# Middleware Tests
# ──────────────────────────────────────────────


class VerificationMiddlewareTests(APITestCase):
    """Test that unverified users are blocked from protected endpoints."""

    def test_unverified_user_blocked(self):
        user = _create_unverified_user(email="blocked@example.com")
        tokens = get_tokens_for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        response = self.client.get(reverse("user-detail"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("not verified", response.json()["message"])

    def test_verified_user_allowed(self):
        user = _create_verified_user(email="allowed@example.com")
        tokens = get_tokens_for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        response = self.client.get(reverse("user-detail"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unauthenticated_user_gets_401(self):
        response = self.client.get(reverse("user-detail"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_auth_endpoints_whitelisted(self):
        response = self.client.post(reverse("signup"), {})
        self.assertNotEqual(response.status_code, status.HTTP_403_FORBIDDEN)


# ──────────────────────────────────────────────
# Model & Validator Tests
# ──────────────────────────────────────────────


class ModelTests(TestCase):
    def test_user_str_with_email(self):
        user = _create_verified_user(email="str@example.com")
        self.assertEqual(str(user), "str@example.com")

    def test_user_str_with_phone(self):
        user = _create_verified_user(email=None, phone="+1112223333")
        self.assertEqual(str(user), "+1112223333")

    def test_otp_is_expired(self):
        user = _create_verified_user(email="otp@example.com")
        otp = OTPVerification.objects.create(
            user=user, otp_hash="h", otp_type=OTPTypes.PHONE_SIGNUP,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertTrue(otp.is_expired)

    def test_otp_not_expired(self):
        user = _create_verified_user(email="otp2@example.com")
        otp = OTPVerification.objects.create(
            user=user, otp_hash="h", otp_type=OTPTypes.PHONE_SIGNUP,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        self.assertFalse(otp.is_expired)

    def test_profile_auto_created(self):
        user = _create_verified_user(email="signal@example.com")
        self.assertTrue(hasattr(user, "profile"))


class ValidatorTests(TestCase):
    def test_valid_phone(self):
        from .validators import validate_phone_number
        self.assertEqual(validate_phone_number("+1234567890"), "+1234567890")

    def test_invalid_phone(self):
        from .validators import validate_phone_number
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            validate_phone_number("not-a-phone")

    def test_valid_password(self):
        from .validators import validate_password_strength
        self.assertEqual(validate_password_strength(TEST_PASSWORD), TEST_PASSWORD)

    def test_weak_password(self):
        from .validators import validate_password_strength
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            validate_password_strength("123")

    def test_valid_email(self):
        from .validators import validate_email_format
        self.assertEqual(validate_email_format("valid@example.com"), "valid@example.com")

    def test_invalid_email(self):
        from .validators import validate_email_format
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            validate_email_format("not-an-email")


class FirebaseLoginTests(APITestCase):
    def setUp(self):
        self.firebase_login_url = reverse("firebase-login")
        self.client = APIClient()

    @patch("api.users.services.firebase_auth.verify_id_token")
    def test_apple_login_missing_nonce(self, mock_verify):
        """Apple login should fail if token has nonce but no nonce provided in API."""
        mock_verify.return_value = {
            "uid": "apple-uid-123",
            "email": "apple@example.com",
            "firebase": {
                "sign_in_provider": "apple.com",
                "sign_in_attributes": {
                    "nonce": "some_hashed_nonce_123"
                }
            }
        }
        response = self.client.post(self.firebase_login_url, {
            "id_token": "dummy_apple_token"
        })
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("Nonce is required", str(response.data))

    @patch("api.users.services.firebase_auth.verify_id_token")
    def test_apple_login_valid_raw_nonce(self, mock_verify):
        """Apple login should succeed if the raw nonce provided hashes to the token's nonce."""
        import hashlib
        raw_nonce = "my_super_secret_nonce"
        hashed_nonce = hashlib.sha256(raw_nonce.encode("utf-8")).hexdigest()
        
        mock_verify.return_value = {
            "uid": "apple-uid-123",
            "email": "apple@example.com",
            "firebase": {
                "sign_in_provider": "apple.com",
                "sign_in_attributes": {
                    "nonce": hashed_nonce
                }
            }
        }
        response = self.client.post(self.firebase_login_url, {
            "id_token": "dummy_apple_token",
            "nonce": raw_nonce
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access_token", response.data["data"])
        
        # User should be created
        user = User.objects.get(firebase_uid="apple-uid-123")
        self.assertTrue(user.is_verified)

    @patch("api.users.services.firebase_auth.verify_id_token")
    def test_apple_login_valid_hashed_nonce(self, mock_verify):
        """Apple login should succeed if the already-hashed nonce is provided."""
        hashed_nonce = "pre_hashed_nonce_string_from_apple"
        
        mock_verify.return_value = {
            "uid": "apple-uid-456",
            "email": "apple2@example.com",
            "firebase": {
                "sign_in_provider": "apple.com",
                "sign_in_attributes": {
                    "nonce": hashed_nonce
                }
            }
        }
        response = self.client.post(self.firebase_login_url, {
            "id_token": "dummy_apple_token_2",
            "nonce": hashed_nonce
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access_token", response.data["data"])

    @patch("api.users.services.firebase_auth.verify_id_token")
    def test_google_login_no_nonce_required(self, mock_verify):
        """Google login should succeed without providing any nonce."""
        mock_verify.return_value = {
            "uid": "google-uid-789",
            "email": "google@example.com",
            "firebase": {
                "sign_in_provider": "google.com",
            }
        }
        response = self.client.post(self.firebase_login_url, {
            "id_token": "dummy_google_token"
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access_token", response.data["data"])

