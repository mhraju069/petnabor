"""
Custom permission classes for the Post app.
Keeping permission logic isolated here removes manual checks from views/services.
"""
from rest_framework import permissions
from django.db.models import Q
from api.friends.models import Friendship
from api.friends.services import is_blocked
from .models import PrivacyChoices


class IsAuthorOrReadOnly(permissions.BasePermission):
    """
    Generic: allow read to all, write only to the object's author/user.
    Supports both `author` and `user` as the ownership field.
    """

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        if hasattr(obj, 'author'):
            return obj.author == request.user
        elif hasattr(obj, 'user'):
            return obj.user == request.user
        return False


class IsPostAuthor(permissions.BasePermission):
    """
    Strict: only the post author can modify or delete.
    Use on Post destroy/update actions.
    """
    message = "You do not have permission to modify this post."

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.author == request.user


class IsCommentAuthorOrPostAuthor(permissions.BasePermission):
    """
    Allows the comment author OR the parent post author to delete a comment.
    This enables post owners to moderate their own content.
    """
    message = "You do not have permission to delete this comment."

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.user == request.user or obj.post.author == request.user


class IsReporterOrAdmin(permissions.BasePermission):
    """
    Allows a reporter to see only their own reports; staff can see all.
    """
    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        return obj.reporter == request.user
def user_can_view_post(user, post):
    """
    Respects Post.privacy levels AND bidirectional block:
    - If either user has blocked the other → deny.
    - PUBLIC: Everyone can see.
    - FRIENDS_ONLY: Author and their friends can see.
    - PRIVATE: Only author can see.
    """
    if post.author_id == user.id:
        return True  # Owner always sees their own post

    # Block check: deny access if blocked in either direction
    if is_blocked(user, post.author):
        return False

    if post.privacy == PrivacyChoices.PUBLIC:
        return True

    if post.privacy == PrivacyChoices.FRIENDS_ONLY:
        return Friendship.objects.filter(
            Q(sender_id=post.author_id, receiver=user) |
            Q(sender=user, receiver_id=post.author_id)
        ).exists()

    return False


class CanViewPost(permissions.BasePermission):
    """Who may see a post: see user_can_view_post."""
    def has_object_permission(self, request, view, obj):
        if not hasattr(obj, 'author'):
            return True  # Not a post or has no author
        return user_can_view_post(request.user, obj)
