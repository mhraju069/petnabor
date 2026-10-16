"""
Serializers for notification settings, FCM devices, and user notifications.
"""
from rest_framework import serializers
from .models import NotificationSettings, FCMDevice, Notifications


class NotificationSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationSettings
        fields = [
            "user",
            "push_notifications",
            "email_notifications",
            "message_notifications",
            "friend_request_notifications",
            "like_notifications",
            "comment_notifications",
            "mention_notifications",
            "meetup_notifications",
            "vendor_post_notifications",
            "product_share_notifications",
            "product_interest_notifications",
            "system_notifications",
            "marketing_notifications",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["user", "created_at", "updated_at"]


class FCMDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = FCMDevice
        fields = ["id", "user", "registration_id", "custom_chat_notifications", "created_at"]
        read_only_fields = ["id", "user", "created_at"]


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notifications
        fields = ["id", "title", "body", "data", "notification_type", "is_read", "created_at"]
        read_only_fields = ["id", "created_at", "title", "body", "data", "notification_type"]
