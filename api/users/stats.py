"""
Profile counters shown under a user's name: friends, posts and meet-ups.
"""

from django.db.models import Q


def get_user_stats(user):
    # Local imports: these apps import users, not the other way round.
    from api.friends.models import Friendship
    from api.meeting.models import Meeting
    from api.post.models import Post

    return {
        "friends": Friendship.objects.filter(
            Q(sender=user) | Q(receiver=user)
        ).count(),
        "posts": Post.objects.filter(author=user, is_deleted=False).count(),
        # Meet-ups that were agreed or happened (not pending/cancelled).
        "meetups": Meeting.objects.filter(
            Q(sender=user) | Q(receiver=user),
            status__in=["ACCEPTED", "COMPLETED"],
        ).count(),
    }
