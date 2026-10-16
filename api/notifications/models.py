"""
Notification, FCM Device, and Notification Settings models.
"""
from django.db import models
import uuid
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _


class NotificationSettings(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_settings",
    )

    push_notifications = models.BooleanField(default=True)
    email_notifications = models.BooleanField(default=True)
    message_notifications = models.BooleanField(default=True)
    friend_request_notifications = models.BooleanField(default=True)
    like_notifications = models.BooleanField(default=True)
    comment_notifications = models.BooleanField(default=True)
    mention_notifications = models.BooleanField(default=True)
    meetup_notifications = models.BooleanField(default=True)
    vendor_post_notifications = models.BooleanField(default=True)
    product_share_notifications = models.BooleanField(default=True)
    product_interest_notifications = models.BooleanField(default=True)
    system_notifications = models.BooleanField(default=True)
    marketing_notifications = models.BooleanField(default=True)
    
    membership_expiry_alerts = models.BooleanField(default=True)
    low_balance_alerts = models.BooleanField(default=False)
    new_order_notifications = models.BooleanField(default=True)
    weekly_performance_report = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


User = get_user_model()


class FCMDevice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="fcm_devices")
    registration_id = models.CharField(max_length=500)  # FCM v1 tokens can exceed 255 chars
    # Set by app versions that draw notifications themselves (the other
    # person's photo, chats stacked per conversation). Those devices get
    # data-only pushes; older versions keep normal system notifications.
    custom_notifications = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.user.email or self.user.phone or str(self.user.id)


class NotificationTypes(models.TextChoices):
    SYSTEM = "system", _("System Update")
    LOGIN = "login", _("New Login Detected")
    SECURITY = "security", _("Security Alert")

    FRIEND_REQUEST = "friend_request", _("New Friend Request")
    FRIEND_ACCEPT = "friend_accept", _("Friend Request Accepted")
    POST_LIKE = "post_like", _("New Like on Post")
    STORY_LIKE = "story_like", _("New Like on Story")
    POST_COMMENT = "post_comment", _("New Comment on Post")
    STORY_COMMENT = "story_comment", _("New Reply on Story")
    POST_MENTION = "post_mention", _("Mentioned in Post")
    STORY_MENTION = "story_mention", _("Mentioned in Story")
    OTP = "otp", _("OTP")
    PASSWORD_RESET = "password_reset", _("Password Reset")
    MESSAGE = "message", _("New Message")

    PET_REMINDER = "pet_reminder", _("Pet Care Reminder")
    PET_MATCH = "pet_match", _("Potential Pet Playmate Found")
    VET_APPOINTMENT = "vet_appointment", _("Vet Appointment Update")

    MEETUP_INVITE = "meetup_invite", _("Meetup Invitation")
    MEETUP_UPDATE = "meetup_update", _("Meetup Details Changed")
    EVENT_REMINDER = "event_reminder", _("Upcoming Pet Event")

    ORDER_STATUS = "order_status", _("Order Progress Update")
    PRODUCT_INTEREST = "product_interest", _("Interest in Your Product")
    SERVICE_BOOKING = "service_booking", _("Service Booking Confirmed")
    REWARD = "reward", _("Reward Available")
    STREAK_BONUS = "streak_bonus", _("Daily Streak Bonus")
    REFERRAL_BONUS = "referral_bonus", _("Referral Bonus Earned")

    PROMOTION = "promotion", _("Special Promotion")
    MARKETING = "marketing", _("Marketing Message")
    INFO = "info", _("General Information")

    MEMBERSHIP_EXPIRY = "membership_expiry", _("Membership Expiry Alert")
    LOW_BALANCE = "low_balance", _("Low Balance Alert")
    NEW_ORDER = "new_order", _("New Order Notification")
    WEEKLY_REPORT = "weekly_report", _("Weekly Performance Report")

    TIP_RECEIVED = "tip_received", _("Tip Received")
    TIP_SENT = "tip_sent", _("Tip Sent")
    TIP_ENABLE_REQUEST = "tip_enable_request", _("Enable Tip Service")


class Notifications(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, related_name="notifications", on_delete=models.CASCADE
    )
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, null=True)
    data = models.JSONField(blank=True, null=True)
    notification_type = models.CharField(
        max_length=20, choices=NotificationTypes.choices, default=NotificationTypes.INFO
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_read"]),
        ]
