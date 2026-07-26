"""
User, Profile, and OTP Verification models.

Designed for 1M+ users with proper indexing and clean naming conventions.
"""

import os
import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.contrib.gis.db import models as gis_models
from django.db import models
from django.utils import timezone


class CustomUserManager(BaseUserManager):
    """Custom manager supporting email/phone-based user creation."""

    def create_user(self, email=None, phone=None, password=None, **extra_fields):
        if not email and not phone:
            raise ValueError("Either email or phone number must be provided.")

        email = self.normalize_email(email) if email else None

        if "username" in extra_fields and not extra_fields["username"]:
            extra_fields.pop("username")

        user = self.model(email=email, phone=phone, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_verified", True)
        extra_fields.setdefault("is_email_verified", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(email=email, password=password, **extra_fields)


class UserTypes(models.TextChoices):
    PETNABOR = "petnabor", "Petnabor"
    PETPAL = "petpal", "Petpal"
    VENDOR = "vendor", "Vendor"
    ADMIN = "admin", "Admin"


class OTPTypes(models.TextChoices):
    PHONE_SIGNUP = "phone_signup", "Phone Signup"
    EMAIL_SIGNUP = "email_signup", "Email Signup"
    PASSWORD_RESET = "password_reset", "Password Reset"


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    email = models.EmailField(unique=True, null=True, blank=True)
    phone = models.CharField(max_length=15, unique=True, null=True, blank=True)

    first_name = models.CharField(max_length=150, blank=True, null=True)
    last_name = models.CharField(max_length=150, blank=True, null=True)

    user_type = models.CharField(
        max_length=20, choices=UserTypes.choices, default=UserTypes.PETNABOR
    )
    agree_to_terms_and_conditions = models.BooleanField(default=False)
    ai_data_consent = models.BooleanField(default=False)
    firebase_uid = models.CharField(max_length=255, unique=True, null=True, blank=True)

    # Verification flags
    is_verified = models.BooleanField(default=False)
    is_email_verified = models.BooleanField(default=False)
    is_phone_verified = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)

    is_petpal = models.BooleanField(default=False)
    is_online = models.BooleanField(default=False)
    last_active = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    username = models.CharField(max_length=150, unique=True, null=True, blank=True)
    password = models.CharField(max_length=128, null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    class Meta:
        indexes = [
            models.Index(fields=["user_type", "is_active"]),
            models.Index(fields=["firebase_uid"]),
            models.Index(fields=["phone"]),
            models.Index(fields=["email"]),
            models.Index(fields=["is_verified"]),
        ]

    objects = CustomUserManager()

    def __str__(self):
        return self.email if self.email else self.phone or str(self.id)

    @property
    def currently_online(self):
        if self.last_active:
            return (timezone.now() - self.last_active).total_seconds() < 300
        return False


def profile_image_path(instance, filename):
    ext = filename.split(".")[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    return os.path.join("profiles/images/", filename)


class Profile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )

    address_street = models.CharField(max_length=255, null=True, blank=True)
    apartment = models.CharField(max_length=100, null=True, blank=True)
    city = models.CharField(max_length=100, null=True, blank=True)
    state = models.CharField(max_length=100, null=True, blank=True)
    zipcode = models.CharField(max_length=20, null=True, blank=True)

    location_point = gis_models.PointField(srid=4326, null=True, blank=True)

    date_of_birth = models.DateField(null=True, blank=True)
    profile_picture = models.ImageField(
        upload_to=profile_image_path, null=True, blank=True
    )
    cover_photo = models.ImageField(upload_to=profile_image_path, null=True, blank=True)
    bio = models.TextField(null=True, blank=True)

    referral_code = models.CharField(max_length=50, unique=True, null=True, blank=True)
    referred_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referrals",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile of {self.user}"


class OTPVerification(models.Model):
    """
    Stores OTP codes for phone and email verification, and password reset.

    OTP codes are hashed before storage for security.
    Composite indexes ensure fast lookups even at 1M+ scale.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="otp_verifications",
    )
    otp_hash = models.CharField(
        max_length=128,
        help_text="SHA-256 hash of the OTP code.",
    )
    otp_type = models.CharField(
        max_length=20,
        choices=OTPTypes.choices,
        default=OTPTypes.PHONE_SIGNUP,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    is_used = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "otp_type", "is_used"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        return f"OTP for {self.user} ({self.otp_type})"

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at


