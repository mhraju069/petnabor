"""
Custom API exceptions for the users app.

All exceptions return clean JSON with appropriate HTTP status codes.
"""

from rest_framework.exceptions import APIException
from rest_framework import status


class AuthenticationFailed(APIException):
    """Raised when login credentials are invalid."""

    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "Invalid credentials. Please check your email/phone and password."
    default_code = "authentication_failed"


class SocialAccountOnly(APIException):
    """Password login / reset on an account created with Google or Apple sign-in."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = (
        "This account was created with Google or Apple sign-in. "
        "Please continue with Google or Apple."
    )
    default_code = "social_account_only"


class AccountNotVerified(APIException):
    """Raised when an unverified user attempts to login or access protected resources."""

    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Your account is not verified. Please verify your email or phone number."
    default_code = "account_not_verified"


class OTPExpired(APIException):
    """Raised when the submitted OTP has expired."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "This OTP has expired. Please request a new one."
    default_code = "otp_expired"


class OTPMaxAttemptsExceeded(APIException):
    """Raised when too many wrong OTP attempts have been made."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = "Too many failed attempts. Please request a new OTP."
    default_code = "otp_max_attempts_exceeded"


class InvalidOTP(APIException):
    """Raised when the submitted OTP code is incorrect."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Invalid OTP code. Please try again."
    default_code = "invalid_otp"


class TwilioServiceError(APIException):
    """Raised when Twilio SMS delivery fails."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "SMS service is temporarily unavailable. Please try again later."
    default_code = "twilio_service_error"


class EmailServiceError(APIException):
    """Raised when email delivery fails."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Email service is temporarily unavailable. Please try again later."
    default_code = "email_service_error"


class AccountAlreadyExists(APIException):
    """Raised when trying to register with an email/phone that already exists."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "An account with this email or phone number already exists."
    default_code = "account_already_exists"


class AccountAlreadyVerified(APIException):
    """Raised when trying to verify an already-verified account."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "This account is already verified."
    default_code = "account_already_verified"
