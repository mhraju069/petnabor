from rest_framework import serializers
from .models import Meeting, MeetingFeedback
from api.users.models import Profile
from django.contrib.auth import get_user_model

from .timing import is_past_everywhere

User = get_user_model()

class MeetingProfileSerializer(serializers.ModelSerializer):
    """
    What the other person in a meeting may see of a profile. Deliberately
    NOT the full profile: home address, apartment, zipcode, exact location,
    date of birth and referral code used to leak to meeting participants.
    The meeting's own address fields carry where to meet.
    """

    class Meta:
        model = Profile
        fields = ["profile_picture", "city", "state", "bio"]
        read_only_fields = fields


class CustomUserSerializer(serializers.ModelSerializer):
    profile = MeetingProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name', 'profile']

class MeetingSerializer(serializers.ModelSerializer):
    sender = CustomUserSerializer(read_only=True)
    receiver_details = CustomUserSerializer(source='receiver', read_only=True)
    # Exposing receiver ID field for creation but with a different name or write-only
    receiver_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), source='receiver', write_only=True
    )

    class Meta:
        model = Meeting
        fields = [
            'id', 'sender', 'receiver_id', 'receiver_details', 
            'visitor_name', 'visitor_phone', 'visit_date', 'visit_time', 'reason', 
            'address_street', 'city', 'state', 'zipcode', 'message', 
            'status', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'sender', 'status', 'created_at', 'updated_at']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        visit_date = attrs.get('visit_date', getattr(self.instance, 'visit_date', None))
        visit_time = attrs.get('visit_time', getattr(self.instance, 'visit_time', None))
        changing_time = self.instance is None or 'visit_date' in attrs or 'visit_time' in attrs
        if changing_time and visit_date and visit_time and is_past_everywhere(visit_date, visit_time):
            raise serializers.ValidationError(
                {"visit_date": "Pick a date and time in the future."}
            )
        return attrs


class MeetingFeedbackSerializer(serializers.ModelSerializer):
    reviewer = CustomUserSerializer(read_only=True)
    reviewee_details = CustomUserSerializer(source='reviewee', read_only=True)

    class Meta:
        model = MeetingFeedback
        fields = [
            'id', 'meeting', 'reviewer', 'reviewee', 'reviewee_details', 
            'rating', 'feedback_text', 'is_public', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'reviewer', 'created_at', 'updated_at']

