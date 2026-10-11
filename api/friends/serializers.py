"""
Serializers for friend requests, friendships, user blocks, and public profiles.
"""
from django.db.models import Q
from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import FriendRequest, Friendship, UserBlock

User = get_user_model()


class NearbyUserSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    first_name = serializers.CharField(read_only=True)
    last_name = serializers.CharField(read_only=True)
    username = serializers.CharField(read_only=True)
    user_type = serializers.CharField(read_only=True)
    distance = serializers.SerializerMethodField()
    profile_picture = serializers.SerializerMethodField()
    location_point = serializers.SerializerMethodField()

    def get_distance(self, obj):
        """Returns distance in kilometers, rounded to 2 decimal places."""
        distance = getattr(obj, "distance", None)
        if distance is None:
            return None
        if hasattr(distance, "km"):
            return round(float(distance.km), 2)
        try:
            return round(float(distance), 2)
        except (TypeError, ValueError):
            return None

    def get_profile_picture(self, obj):
        if hasattr(obj, "profile") and obj.profile.profile_picture:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.profile.profile_picture.url)
            return obj.profile.profile_picture.url
        return None

    def get_location_point(self, obj):
        """Returns [latitude, longitude] to match frontend convention."""
        try:
            point = obj.profile.location_point
            if point:
                return [round(point.y, 4), round(point.x, 4)]  # [lat, lng]
        except Exception:
            pass
        return None


class FriendRequestSerializer(serializers.ModelSerializer):
    sender_details = NearbyUserSerializer(source="sender", read_only=True)
    receiver_details = NearbyUserSerializer(source="receiver", read_only=True)

    class Meta:
        model = FriendRequest
        fields = [
            "id",
            "sender",
            "sender_details",
            "receiver",
            "receiver_details",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "sender", "status", "created_at", "updated_at"]


class FriendshipSerializer(serializers.ModelSerializer):
    friend_details = serializers.SerializerMethodField()

    class Meta:
        model = Friendship
        fields = ["id", "sender", "receiver", "friend_details", "created_at"]
        read_only_fields = ["id", "sender", "receiver", "created_at"]

    def get_friend_details(self, obj):
        request = self.context.get("request")
        current_user = request.user if request else None

        friend = obj.receiver if obj.sender == current_user else obj.sender
        return NearbyUserSerializer(friend, context={"request": request}).data


class UserBlockSerializer(serializers.ModelSerializer):
    blocked_user_details = NearbyUserSerializer(source="blocked_user", read_only=True)

    class Meta:
        model = UserBlock
        fields = ["id", "blocker", "blocked_user", "blocked_user_details", "created_at"]
        read_only_fields = ["id", "blocker", "created_at"]


class UserActionSerializer(serializers.Serializer):
    """Used for actions requiring just a user_id: unfriend, block, unblock, send/accept/reject/cancel friend request"""

    user_id = serializers.UUIDField(required=True)


class PublicUserSerializer(serializers.ModelSerializer):
    """
    Serializer for public user profiles.
    Hides sensitive data like email/phone and precise address.
    """

    bio = serializers.CharField(source="profile.bio", read_only=True)
    city = serializers.CharField(source="profile.city", read_only=True)
    state = serializers.CharField(source="profile.state", read_only=True)
    profile_picture = serializers.SerializerMethodField()
    cover_photo = serializers.SerializerMethodField()
    friendship_status = serializers.SerializerMethodField()
    stats = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "first_name",
            "last_name",
            "username",
            "user_type",
            "bio",
            "city",
            "state",
            "profile_picture",
            "cover_photo",
            "friendship_status",
            "stats",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_stats(self, obj):
        from api.users.stats import get_user_stats

        return get_user_stats(obj)

    def get_profile_picture(self, obj):
        if hasattr(obj, "profile") and obj.profile.profile_picture:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.profile.profile_picture.url)
            return obj.profile.profile_picture.url
        return None

    def get_cover_photo(self, obj):
        if hasattr(obj, "profile") and obj.profile.cover_photo:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.profile.cover_photo.url)
            return obj.profile.cover_photo.url
        return None

    def get_friendship_status(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None

        current_user = request.user
        if current_user == obj:
            return "self"

        # Check existing friendship
        if Friendship.objects.filter(
            Q(sender=current_user, receiver=obj) | Q(sender=obj, receiver=current_user)
        ).exists():
            return "friends"

        # Check pending requests
        pending_out = FriendRequest.objects.filter(
            sender=current_user, receiver=obj, status="pending"
        ).exists()
        if pending_out:
            return "request_sent"

        pending_in = FriendRequest.objects.filter(
            sender=obj, receiver=current_user, status="pending"
        ).exists()
        if pending_in:
            return "request_received"

        return "none"


class SuggestedUserSerializer(PublicUserSerializer):
    """
    Serializer for suggested friends.
    Includes an additional mutual_friends_count field and score.
    """
    mutual_friends_count = serializers.IntegerField(read_only=True)
    score = serializers.SerializerMethodField()

    # Lists of suggestions: skip the per-user profile counters (3 queries each).
    stats = None

    class Meta(PublicUserSerializer.Meta):
        fields = [
            f for f in PublicUserSerializer.Meta.fields if f != "stats"
        ] + ["mutual_friends_count", "score"]

    def get_score(self, obj):
        mutual_count = getattr(obj, "mutual_friends_count", 0)
        score = mutual_count * 10.0
        
        dist_mi = getattr(obj, "distance", None)
        if dist_mi is not None:
            val_mi = getattr(dist_mi, "mi", dist_mi)
            try:
                val_mi = float(val_mi)
                if val_mi <= 10:
                    score += 20.0
                elif val_mi <= 50:
                    score += 10.0
                elif val_mi <= 100:
                    score += 5.0
            except (TypeError, ValueError):
                pass
        return score

    def get_friendship_status(self, obj):
        # We explicitly exclude existing friends and pending requests in the suggestion 
        # algorithm, so it's always 'none'. This avoids 3 N+1 queries per user.
        return "none"

