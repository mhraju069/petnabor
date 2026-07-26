"""
Authentication services: signup, login, OTP (phone + email), password reset, Firebase.

All business logic is centralized here. Views are thin wrappers.
Uses select_related/prefetch_related to avoid N+1 queries.
"""

import hashlib
import logging
import random
import string
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.utils import timezone
from firebase_admin import auth as firebase_auth
from rest_framework_simplejwt.tokens import RefreshToken

from api.notifications.models import NotificationTypes
from api.notifications.services import send_notification
from .tasks import send_otp_email_task, send_otp_sms_task

from .exceptions import (
    AccountAlreadyExists,
    AccountAlreadyVerified,
    AccountNotVerified,
    AuthenticationFailed,
    InvalidOTP,
    OTPExpired,
    OTPMaxAttemptsExceeded,
)
from .models import OTPTypes, OTPVerification, Profile

logger = logging.getLogger(__name__)

User = get_user_model()



def get_tokens_for_user(user):
    """Generate JWT access and refresh tokens for a user."""
    refresh = RefreshToken.for_user(user)
    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
    }



def _generate_otp_code():
    """Generate a random N-digit OTP code (default: 4 digits)."""
    length = getattr(settings, "OTP_LENGTH", 4)
    return "".join(random.choices(string.digits, k=length))


def _hash_otp(otp_code):
    """SHA-256 hash an OTP code before storing in the database."""
    return hashlib.sha256(otp_code.encode("utf-8")).hexdigest()


def _invalidate_existing_otps(user, otp_type):
    """Mark all unused OTPs of a given type as used for the specified user."""
    OTPVerification.objects.filter(user=user, otp_type=otp_type, is_used=False).update(
        is_used=True
    )



def create_otp_for_user(user, otp_type=OTPTypes.PHONE_SIGNUP):
    """
    Create a new OTP for a user.

    Invalidates any existing unused OTPs of the same type,
    generates a new one, stores it hashed, and returns the plain code.
    """
    _invalidate_existing_otps(user, otp_type)

    otp_code = _generate_otp_code()
    expiry_minutes = getattr(settings, "OTP_EXPIRY_MINUTES", 5)

    OTPVerification.objects.create(
        user=user,
        otp_hash=_hash_otp(otp_code),
        otp_type=otp_type,
        expires_at=timezone.now() + timezone.timedelta(minutes=expiry_minutes),
    )
    return otp_code


def _verify_otp(user, otp_code, otp_type):
    """
    Verify an OTP for a user (works for both phone and email OTPs).

    Checks expiry, max attempts, and OTP hash match.
    Returns True on success.
    """
    max_attempts = getattr(settings, "OTP_MAX_ATTEMPTS", 5)

    latest_otp = (
        OTPVerification.objects.filter(
            user=user,
            otp_type=otp_type,
            is_used=False,
        )
        .order_by("-created_at")
        .first()
    )

    if not latest_otp:
        raise InvalidOTP(detail="No active OTP found. Please request a new one.")

    if latest_otp.is_expired:
        latest_otp.is_used = True
        latest_otp.save(update_fields=["is_used"])
        raise OTPExpired()

    if latest_otp.attempt_count >= max_attempts:
        latest_otp.is_used = True
        latest_otp.save(update_fields=["is_used"])
        raise OTPMaxAttemptsExceeded()

    if _hash_otp(otp_code) != latest_otp.otp_hash:
        latest_otp.attempt_count += 1
        latest_otp.save(update_fields=["attempt_count"])
        remaining = max_attempts - latest_otp.attempt_count
        raise InvalidOTP(detail=f"Invalid OTP code. {remaining} attempt(s) remaining.")

    # OTP is valid — mark as used
    latest_otp.is_used = True
    latest_otp.save(update_fields=["is_used"])
    return True



def send_phone_otp(phone, otp_code):
    """Trigger asynchronous SMS delivery via Celery."""
    expiry_minutes = getattr(settings, "OTP_EXPIRY_MINUTES", 5)
    send_otp_sms_task.delay(phone, otp_code, expiry_minutes)


def verify_phone_otp(user, otp_code):
    """Verify phone OTP and mark user as phone-verified."""
    _verify_otp(user, otp_code, OTPTypes.PHONE_SIGNUP)

    user.is_phone_verified = True
    user.is_verified = True
    user.save(update_fields=["is_phone_verified", "is_verified"])
    return True



def send_email_otp(email, otp_code):
    """Trigger asynchronous Email delivery via Celery."""
    expiry_minutes = getattr(settings, "OTP_EXPIRY_MINUTES", 5)
    send_otp_email_task.delay(email, otp_code, expiry_minutes)


def verify_email_otp(user, otp_code):
    """Verify email OTP and mark user as email-verified."""
    _verify_otp(user, otp_code, OTPTypes.EMAIL_SIGNUP)

    user.is_email_verified = True
    user.is_verified = True
    user.save(update_fields=["is_email_verified", "is_verified"])
    return True



def signup_user(
    email=None,
    phone=None,
    password=None,
    first_name="",
    last_name="",
    user_type="petnabor",
    agree_to_terms_and_conditions=False,
    ai_data_consent=False,
    referred_by_code=None,
):
    """
    Register a new user with email or phone.

    - If phone is provided: sends OTP via SMS
    - If email is provided: sends OTP via email
    - User starts as unverified

    Returns:
        tuple: (user, verification_type) — 'email' or 'phone'
    """
    # Check for existing users
    if email:
        email = email.strip().lower()
        if User.objects.filter(email=email).exists():
            raise AccountAlreadyExists(
                detail="An account with this email already exists."
            )

    if phone:
        phone = phone.strip()
        if User.objects.filter(phone=phone).exists():
            raise AccountAlreadyExists(
                detail="An account with this phone number already exists."
            )

    # Generate username
    if email:
        base_username = email.split("@")[0]
    elif phone:
        base_username = f"user_{str(uuid.uuid4())[:8]}"
    else:
        base_username = f"user_{str(uuid.uuid4())[:8]}"

    # Ensure uniqueness
    username = base_username
    counter = 1
    while User.objects.filter(username=username).exists():
        username = f"{base_username}_{counter}"
        counter += 1

    try:
        user = User.objects.create_user(
            email=email,
            phone=phone,
            password=password,
            username=username,
            first_name=first_name,
            last_name=last_name,
            user_type=user_type,
            agree_to_terms_and_conditions=agree_to_terms_and_conditions,
            ai_data_consent=ai_data_consent,
            is_verified=False,
            is_email_verified=False,
            is_phone_verified=False,
        )
    except IntegrityError:
        raise AccountAlreadyExists()

    # Link referrer if code exists
    if referred_by_code:
        referrer_profile = Profile.objects.filter(referral_code=referred_by_code.upper()).first()
        if referrer_profile:
            # Profile is created via signal on User creation
            user_profile = getattr(user, "profile", None)
            if user_profile:
                user_profile.referred_by = referrer_profile.user
                user_profile.save(update_fields=["referred_by"])

    # Send verification OTP based on registration method
    verification_type = None

    if phone:
        otp_code = create_otp_for_user(user, OTPTypes.PHONE_SIGNUP)
        send_phone_otp(phone, otp_code)
        verification_type = "phone"

    if email:
        otp_code = create_otp_for_user(user, OTPTypes.EMAIL_SIGNUP)
        send_email_otp(email, otp_code)
        verification_type = verification_type or "email"

    return user, verification_type


def login_user(email_or_phone, password):
    """
    Authenticate a user by email or phone + password.

    Enforces verification: unverified users cannot login.

    Returns:
        tuple: (tokens_dict, user)
    """
    user = None

    # Determine if input is email or phone
    if "@" in email_or_phone:
        user = (
            User.objects.select_related("profile")
            .filter(email=email_or_phone.strip().lower())
            .first()
        )
    else:
        # Handles URL-encoded '+' symbols which get converted to ' ' by HTTP automatically
        normalized_phone = email_or_phone.strip().replace(" ", "+")
        # Ensure it has a plus sign if it's missing but looks like an international number
        if not normalized_phone.startswith("+") and normalized_phone.isdigit():
            normalized_phone = "+" + normalized_phone
            
        user = (
            User.objects.select_related("profile")
            .filter(phone=normalized_phone)
            .first()
        )

    if not user:
        raise AuthenticationFailed()

    if not user.check_password(password):
        raise AuthenticationFailed()

    if not user.is_active:
        raise AuthenticationFailed(
            detail="This account has been deactivated. Please contact support."
        )

    if not user.is_verified:
        raise AccountNotVerified()

    tokens = get_tokens_for_user(user)

    # Send login notification (async via Celery)
    try:
        # send_notification(
        #     user_id=user.id,
        #     title="New Login Detected",
        #     body="A new login session was established for your account.",
        #     data={"type": "login"},
        #     notification_type=NotificationTypes.LOGIN,
        # )
        pass
    except Exception:
        # Don't block login if notification fails
        logger.warning("Failed to send login notification for user %s", user.id)

    return tokens, user



def request_password_reset(email_or_phone):
    """
    Send a password reset OTP via email or phone.

    Returns the delivery method ('email' or 'phone').
    """
    user = None

    if "@" in email_or_phone:
        user = User.objects.filter(email=email_or_phone.strip().lower()).first()
    else:
        user = User.objects.filter(phone=email_or_phone.strip()).first()

    if not user:
        raise AuthenticationFailed(
            detail="No account found with this email or phone number."
        )

    otp_code = create_otp_for_user(user, OTPTypes.PASSWORD_RESET)

    if user.email and "@" in email_or_phone:
        _send_password_reset_email(user.email, otp_code)
        return "email"
    elif user.phone:
        send_phone_otp(user.phone, otp_code)
        return "phone"
    else:
        raise AuthenticationFailed(
            detail="No contact method available for this account."
        )


def confirm_password_reset(email_or_phone, otp_code, new_password):
    """
    Verify the password reset OTP and set the new password.
    """
    user = None

    if "@" in email_or_phone:
        user = User.objects.filter(email=email_or_phone.strip().lower()).first()
    else:
        user = User.objects.filter(phone=email_or_phone.strip()).first()

    if not user:
        raise AuthenticationFailed(
            detail="No account found with this email or phone number."
        )

    _verify_otp(user, otp_code, OTPTypes.PASSWORD_RESET)

    user.set_password(new_password)
    user.save(update_fields=["password"])
    
    try:
        send_notification(
            user_id=user.id,
            title="Password Reset Successful",
            body=f"Your password has been successfully reset.",
            notification_type=NotificationTypes.SYSTEM,
            data={"type": "password_reset"},
        )
    except Exception:
        logger.warning("Failed to send password reset notification for user %s", user.id)

    return True


def _send_password_reset_email(email, otp_code):
    """Trigger asynchronous Password Reset Email delivery via Celery."""
    expiry_minutes = getattr(settings, "OTP_EXPIRY_MINUTES", 5)
    send_otp_email_task.delay(email, otp_code, expiry_minutes, is_password_reset=True)



def firebase_login_service(
    id_token,
    first_name="",
    last_name="",
    user_type="petnabor",
    agree_to_terms_and_conditions=False,
    ai_data_consent=False,
    referred_by_code=None,
    nonce="",
):
    """
    Authenticate via Firebase ID token.

    Creates the user if they don't exist (auto-verified via Firebase).
    """
    try:
        decoded_token = firebase_auth.verify_id_token(id_token)
    except Exception as exc:
        logger.warning("Firebase token verification failed: %s", exc)
        raise AuthenticationFailed(detail="Invalid Firebase token.")

    firebase_data = decoded_token.get("firebase", {})
    sign_in_provider = firebase_data.get("sign_in_provider", "")

    # For Apple Sign-In, verify nonce if provided
    if sign_in_provider == "apple.com":
        token_nonce = firebase_data.get("sign_in_attributes", {}).get("nonce")
        if token_nonce:
            if not nonce:
                raise AuthenticationFailed(detail="Nonce is required for Apple Sign-In verification.")
            import hashlib
            hashed_nonce = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
            # The client might send the raw nonce or the already hashed nonce
            if nonce != token_nonce and hashed_nonce != token_nonce:
                raise AuthenticationFailed(detail="Invalid nonce for Apple Sign-In.")

    uid = decoded_token.get("uid")
    email = decoded_token.get("email")
    phone = decoded_token.get("phone_number")
    firebase_name = decoded_token.get("name", "")

    if not uid:
        raise AuthenticationFailed(detail="Invalid Firebase token: Missing UID.")

    if not email and not phone:
        raise AuthenticationFailed(
            detail="Invalid Firebase token: Missing email or phone number."
        )

    if firebase_name and not first_name:
        name_parts = firebase_name.split(" ", 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""

    # Look up user by firebase_uid first, then by email/phone
    user = User.objects.select_related("profile").filter(firebase_uid=uid).first()

    if not user:
        if email:
            user = User.objects.select_related("profile").filter(email=email).first()
        elif phone:
            user = User.objects.select_related("profile").filter(phone=phone).first()

    if user:
        # Link Firebase UID if not already linked
        if not user.firebase_uid:
            user.firebase_uid = uid
            user.is_verified = True
            user.save(update_fields=["firebase_uid", "is_verified"])
        is_new_user = False
    else:
        # Create new user (Firebase users are auto-verified)
        base_username = (
            email.split("@")[0] if email else f"user_{str(uuid.uuid4())[:8]}"
        )
        username = base_username
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}_{counter}"
            counter += 1

        user = User.objects.create(
            email=email,
            username=username,
            phone=phone,
            first_name=first_name,
            last_name=last_name,
            user_type=user_type,
            agree_to_terms_and_conditions=agree_to_terms_and_conditions,
            ai_data_consent=ai_data_consent,
            firebase_uid=uid,
            is_verified=True,
            is_email_verified=bool(email),
            is_phone_verified=bool(phone),
        )
        is_new_user = True
        user.set_unusable_password()
        user.save()

        # Link referrer if code exists (for new users only)
        if referred_by_code:
            referrer_profile = Profile.objects.filter(referral_code=referred_by_code.upper()).first()
            if referrer_profile:
                user_profile = getattr(user, "profile", None)
                if user_profile:
                    user_profile.referred_by = referrer_profile.user
                    user_profile.save(update_fields=["referred_by"])

    tokens = get_tokens_for_user(user)

    try:
        # send_notification(
        #     user_id=user.id,
        #     title="New Login Detected",
        #     body="A new login session was established for your account.",
        #     data={"type": "login"},
        #     notification_type=NotificationTypes.LOGIN,
        # )
        pass
    except Exception:
        logger.warning("Failed to send login notification for user %s", user.id)

    return tokens, user, is_new_user



def resend_phone_otp(phone):
    """Resend OTP to an existing unverified user's phone."""
    user = User.objects.filter(phone=phone.strip()).first()

    if not user:
        raise AuthenticationFailed(detail="No account found with this phone number.")

    if user.is_phone_verified:
        raise AccountAlreadyVerified(detail="This phone number is already verified.")

    otp_code = create_otp_for_user(user, OTPTypes.PHONE_SIGNUP)
    send_phone_otp(phone, otp_code)
    return True


def resend_email_otp(email):
    """Resend OTP to an existing unverified user's email."""
    user = User.objects.filter(email=email.strip().lower()).first()

    if not user:
        raise AuthenticationFailed(detail="No account found with this email address.")

    if user.is_email_verified:
        raise AccountAlreadyVerified(detail="This email is already verified.")

    otp_code = create_otp_for_user(user, OTPTypes.EMAIL_SIGNUP)
    send_email_otp(email, otp_code)
    return True
