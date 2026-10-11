"""
Business logic for friendships, friend requests, and user blocks.
"""
from django.db.models import Q
from django.contrib.auth import get_user_model
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.measure import D
from rest_framework.exceptions import ValidationError, NotFound, PermissionDenied
from .models import FriendRequest, Friendship, UserBlock
from api.notifications.services import send_notification
from api.notifications.models import NotificationTypes

User = get_user_model()


def _invalidate_message_permission(user_a_id, user_b_id):
    """Lazily import and invalidate the messaging permission cache for a user pair."""
    try:
        from api.messaging.services import invalidate_messaging_permission
        invalidate_messaging_permission(user_a_id, user_b_id)
    except Exception:
        pass  # messaging app may not be installed in all test environments


def is_blocked(user_a, user_b):
    """Helper method to determine if a block exists in either direction."""
    return UserBlock.objects.filter(
        Q(blocker=user_a, blocked_user=user_b) | Q(blocker=user_b, blocked_user=user_a)
    ).exists()


def send_friend_request(sender, receiver_id):
    if str(sender.id) == str(receiver_id):
        raise ValidationError("Cannot send friend request to yourself")

    try:
        receiver = User.objects.get(id=receiver_id)
    except User.DoesNotExist:
        raise NotFound("User not found")

    # Check if blocked
    if is_blocked(sender, receiver):
        raise ValidationError("Cannot send request")

    # Check if already friends
    if Friendship.objects.filter(
        Q(sender=sender, receiver=receiver) | Q(sender=receiver, receiver=sender)
    ).exists():
        raise ValidationError("Already friends")

    # Check if request already exists
    existing_req = FriendRequest.objects.filter(
        sender=sender, receiver=receiver, status="pending"
    ).first()
    if existing_req:
        raise ValidationError("Friend request already sent")

    # Reverse pending request exists?
    reverse_req = FriendRequest.objects.filter(
        sender=receiver, receiver=sender, status="pending"
    ).first()
    if reverse_req:
        # Automatically accept — create Friendship, then clean up the request
        Friendship.objects.create(sender=receiver, receiver=sender)

        # Notify the original sender (receiver of this new request) that it was accepted.
        # Use accepter_id so the frontend can deeplink to the accepter's profile.
        sender_display = f"{sender.first_name} {sender.last_name}".strip() or sender.username or "Someone"
        send_notification(
            user_id=receiver.id,
            title=sender_display,
            body="accepted your friend request.",
            notification_type=NotificationTypes.FRIEND_ACCEPT,
            data={"accepter_id": str(sender.id)},
        )
        reverse_req.delete()
        return None, True  # True means automatically accepted

    freq = FriendRequest.objects.create(sender=sender, receiver=receiver, status="pending")

    # Notify the receiver about the new friend request (include IDs for frontend deeplink)
    sender_display = f"{sender.first_name} {sender.last_name}".strip() or sender.username or "Someone"
    send_notification(
        user_id=receiver.id,
        title=sender_display,
        body="sent you a friend request.",
        notification_type=NotificationTypes.FRIEND_REQUEST,
        data={"sender_id": str(sender.id), "request_id": str(freq.id)},
    )
    return freq, False


def accept_friend_request(user, friend_request):
    if friend_request.receiver != user:
        raise PermissionDenied("Not authorized")
    if friend_request.status != "pending":
        raise ValidationError("Request already processed")

    if is_blocked(user, friend_request.sender):
        raise ValidationError("Cannot accept request, user is blocked")

    sender_id = friend_request.sender_id

    Friendship.objects.get_or_create(
        sender=friend_request.sender, receiver=friend_request.receiver
    )

    # Clean up the accepted request — data lives in Friendship model now
    friend_request.delete()

    # Invalidate messaging permission cache for this new friendship
    _invalidate_message_permission(user.id, sender_id)

    # Notify the sender that the request was accepted.
    # Use accepter_id so the frontend can deeplink to the accepter's profile.
    accepter_display = f"{user.first_name} {user.last_name}".strip() or user.username or "Someone"
    send_notification(
        user_id=sender_id,
        title=accepter_display,
        body="accepted your friend request.",
        notification_type=NotificationTypes.FRIEND_ACCEPT,
        data={"accepter_id": str(user.id)},
    )
    return True


def reject_friend_request(user, friend_request):
    if friend_request.receiver != user:
        raise PermissionDenied("Not authorized")
    if friend_request.status != "pending":
        raise ValidationError("Request already processed")

    # Delete the request to keep the database clean
    friend_request.delete()
    return friend_request


def cancel_friend_request(user, friend_request):
    if friend_request.sender != user:
        raise PermissionDenied("Not authorized")
    if friend_request.status != "pending":
        raise ValidationError("Cannot cancel processed request")

    # Delete the pending request
    friend_request.delete()
    return True


def remove_friend(user, friend_id):
    try:
        friend = User.objects.get(id=friend_id)
    except User.DoesNotExist:
        raise NotFound("User not found")

    # Delete the Friendship record
    deleted, _ = Friendship.objects.filter(
        Q(sender=user, receiver=friend) | Q(sender=friend, receiver=user)
    ).delete()

    if deleted == 0:
        raise NotFound("Friendship not found")

    # Invalidate messaging permission cache
    _invalidate_message_permission(user.id, friend.id)

    # Also clean up any lingering FriendRequests
    FriendRequest.objects.filter(
        Q(sender=user, receiver=friend) | Q(sender=friend, receiver=user)
    ).delete()

    return True


def block_user(blocker, blocked_user_id):
    try:
        blocked_user = User.objects.get(id=blocked_user_id)
    except User.DoesNotExist:
        raise NotFound("User not found")

    UserBlock.objects.get_or_create(blocker=blocker, blocked_user=blocked_user)

    # Invalidate messaging permission cache
    _invalidate_message_permission(blocker.id, blocked_user.id)

    # Remove friendship if exists
    Friendship.objects.filter(
        Q(sender=blocker, receiver=blocked_user)
        | Q(sender=blocked_user, receiver=blocker)
    ).delete()

    # Cancel pending requests
    FriendRequest.objects.filter(
        Q(sender=blocker, receiver=blocked_user) | Q(sender=blocked_user, receiver=blocker),
        status="pending",
    ).delete()
    return True


def unblock_user(blocker, blocked_user_id):
    deleted, _ = UserBlock.objects.filter(
        blocker=blocker, blocked_user_id=blocked_user_id
    ).delete()
    if deleted == 0:
        raise NotFound("Block record not found")
    # Invalidate messaging permission cache
    _invalidate_message_permission(blocker.id, blocked_user_id)
    return True


def get_nearby_users(
    current_user,
    user_type=None,
    radius=50.0,
    search_query="",
    include_friends=True,
    city=None,
    state=None,
):
    user_point = None

    if hasattr(current_user, "profile") and current_user.profile.location_point:
        user_point = current_user.profile.location_point

    # Exclude blocked users
    blocked_ids = list(
        UserBlock.objects.filter(blocker=current_user).values_list(
            "blocked_user_id", flat=True
        )
    )
    blocked_by_ids = list(
        UserBlock.objects.filter(blocked_user=current_user).values_list(
            "blocker_id", flat=True
        )
    )

    # Exclude existing friends
    friendships = Friendship.objects.filter(Q(sender=current_user) | Q(receiver=current_user))
    friend_ids = [
        f.receiver_id if f.sender_id == current_user.id else f.sender_id
        for f in friendships
    ]

    exclude_ids = set(blocked_ids + blocked_by_ids)
    if not include_friends:
        exclude_ids.update(friend_ids)

    users_query = (
        User.objects.select_related("profile")
        .filter(is_active=True)
        .exclude(id__in=exclude_ids)
        # Never list the searcher in their own results.
        .exclude(id=current_user.id)
    )

    if user_type:
        users_query = users_query.filter(user_type=user_type)

    if city:
        users_query = users_query.filter(profile__city__icontains=city)

    if state:
        users_query = users_query.filter(profile__state__icontains=state)

    if search_query:
        users_query = users_query.filter(
            Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
            | Q(username__icontains=search_query)
        )

    if user_point:
        # When searching by name, skip radius — show results from all locations.
        # When NO name search, apply the radius filter (distance-based discovery).
        apply_radius = radius and not search_query

        if apply_radius:
            users_query = users_query.filter(
                profile__location_point__distance_lte=(user_point, D(km=radius))
            )

        return users_query.annotate(
            distance=Distance("profile__location_point", user_point)
        ).order_by("distance")
    else:
        # No location set — global search ordered by join date for consistent pagination
        return users_query.select_related("profile").order_by("date_joined")


def get_suggested_friends(current_user, limit=20):
    from django.db.models import Count, IntegerField, F, FloatField, ExpressionWrapper

    user_point = None
    if hasattr(current_user, "profile") and current_user.profile.location_point:
        user_point = current_user.profile.location_point

    # 1. Gather exclusions via DB Subqueries to avoid loading ANY lists into Python memory
    blocked_ids = UserBlock.objects.filter(blocker=current_user).values("blocked_user_id")
    blocked_by_ids = UserBlock.objects.filter(blocked_user=current_user).values("blocker_id")
    pending_sent = FriendRequest.objects.filter(sender=current_user, status="pending").values("receiver_id")
    pending_received = FriendRequest.objects.filter(receiver=current_user, status="pending").values("sender_id")
    
    # friend_ids need Python map because Friendship model stores users dynamically as sender/receiver
    friendships = Friendship.objects.filter(Q(sender=current_user) | Q(receiver=current_user))
    friend_ids = [f.receiver_id if f.sender_id == current_user.id else f.sender_id for f in friendships]

    # 2. Base Queryset (select_related fixes N+1 query)
    users_query = User.objects.select_related("profile").filter(
        is_active=True
    ).exclude(
        Q(id__in=blocked_ids) |
        Q(id__in=blocked_by_ids) |
        Q(id__in=pending_sent) |
        Q(id__in=pending_received) |
        Q(id__in=friend_ids) |
        Q(id=current_user.id)
    )

    # 3. Annotate count in DB (memory efficient)
    users_query = users_query.annotate(
        mutual_friends_initiated=Count(
            "friendships_initiated",
            filter=Q(friendships_initiated__receiver_id__in=friend_ids),
            distinct=True
        ),
        mutual_friends_received=Count(
            "friendships_received",
            filter=Q(friendships_received__sender_id__in=friend_ids),
            distinct=True
        )
    ).annotate(
        mutual_friends_count=ExpressionWrapper(
            F("mutual_friends_initiated") + F("mutual_friends_received"),
            output_field=IntegerField()
        )
    )

    # 4. Sorting exclusively in DB instead of Python so we can safely yield/paginate
    if user_point:
        users_query = users_query.annotate(
            distance=Distance("profile__location_point", user_point)
        ).order_by("-mutual_friends_count", "distance")
    else:
        users_query = users_query.order_by("-mutual_friends_count")

    # Return the raw QuerySet for the View to lazily evaluate via Pagination!
    # By returning this, the DB handles chunk sizes, counting, and slicing effortlessly.
    return users_query

