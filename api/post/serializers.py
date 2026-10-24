"""
Serializers for Post, Comment, Like, and Media.
Key improvements:
- replies_count uses denormalized model field directly (no SerializerMethodField extra hit)
- thumbnail_file exposed in PostMediaSerializer
- is_liked computed from prefetched user_reactions (zero extra DB hits)
- PostCreateUpdateSerializer validates MIME type and file size from settings
"""

from django.conf import settings
from rest_framework import serializers

from .models import (
    Hashtag, Post, PostComment, PostLike, PostMedia, SavedPost,
)
from api.users.models import User


# ──────────────────────────────────────────────
# Nested / Shared Serializers
# ──────────────────────────────────────────────

class AuthorBasicSerializer(serializers.ModelSerializer):
    """Lightweight user embedding — avoids N+1 and data leakage."""
    profile_picture = serializers.ImageField(source='profile.profile_picture', read_only=True)

    class Meta:
        model = User
        fields = ['id', 'first_name', 'last_name', 'username', 'profile_picture']


class HashtagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Hashtag
        fields = ['id', 'name']


class PostMediaSerializer(serializers.ModelSerializer):
    """Exposes all three size variants + processing status."""

    class Meta:
        model = PostMedia
        fields = [
            'id', 'media_type', 'file', 'medium_file', 'thumbnail_file',
            'processing_status', 'order', 'created_at',
        ]
        read_only_fields = ['id', 'processing_status', 'created_at']


class PostLikeSerializer(serializers.ModelSerializer):
    user = AuthorBasicSerializer(read_only=True)

    class Meta:
        model = PostLike
        fields = ['id', 'user', 'reaction_type', 'created_at']
        read_only_fields = ['id', 'user', 'created_at']


class PostCommentSerializer(serializers.ModelSerializer):
    user = AuthorBasicSerializer(read_only=True)
    # Read directly from the denormalized model field — no extra DB hit
    reply_count = serializers.IntegerField(source='replies_count', read_only=True)
    # Friends tagged in the text as "@First Last" (the app highlights them).
    mentions = AuthorBasicSerializer(many=True, read_only=True)
    # Who to tag: friend ids. Only friends who can see the post are kept.
    mention_ids = serializers.ListField(
        child=serializers.UUIDField(), write_only=True, required=False, max_length=20
    )

    class Meta:
        model = PostComment
        fields = [
            'id', 'post', 'user', 'parent_comment',
            'comment_text', 'media_file', 'is_edited',
            'created_at', 'updated_at', 'reply_count',
            'mentions', 'mention_ids',
        ]
        read_only_fields = ['id', 'user', 'is_edited', 'created_at', 'updated_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance:
            self.fields['post'].read_only = True
            self.fields['parent_comment'].read_only = True


# ──────────────────────────────────────────────
# Post Write Serializer
# ──────────────────────────────────────────────

class PostCreateUpdateSerializer(serializers.ModelSerializer):
    """
    Handles post creation/update input.
    Media, mentions, and hashtags are handled by the service layer.
    Full server-side MIME + size validation here.
    """

    class Meta:
        model = Post
        fields = ['id', 'content_text', 'location_point', 'privacy']

    def validate(self, attrs):
        request = self.context.get('request')
        if not (request and hasattr(request, 'FILES')):
            return attrs

        files = request.FILES.getlist('media')
        max_bytes = settings.POST_MEDIA_MAX_SIZE_BYTES
        allowed_ext = settings.POST_ALLOWED_EXTENSIONS
        allowed_mime = settings.POST_ALLOWED_MIME_TYPES

        for f in files:
            # 1. Size check
            if f.size > max_bytes:
                raise serializers.ValidationError({
                    "media": f"'{f.name}' exceeds the {max_bytes // (1024 * 1024)} MB size limit."
                })

            # 2. Extension check
            _, ext = f.name.rsplit('.', 1) if '.' in f.name else ('', '')
            if ext.lower() not in allowed_ext:
                raise serializers.ValidationError({
                    "media": f"'{f.name}' has an unsupported extension ('{ext}')."
                })

            # 3. MIME type check (cannot be spoofed by renaming)
            content_type = getattr(f, 'content_type', '')
            if content_type not in allowed_mime:
                raise serializers.ValidationError({
                    "media": f"'{f.name}' has an unsupported MIME type ('{content_type}')."
                })

        return attrs


# ──────────────────────────────────────────────
# Post Read Serializers
# ──────────────────────────────────────────────

class PostListSerializer(serializers.ModelSerializer):
    """
    Optimized for feed/list views.
    - `is_liked` is derived from the `user_reactions` Prefetch attribute (zero extra DB hits).
    - All nested objects are served from prefetch caches.
    """
    author = AuthorBasicSerializer(read_only=True)
    media = PostMediaSerializer(many=True, read_only=True)
    hashtags = HashtagSerializer(many=True, read_only=True)
    mentions = AuthorBasicSerializer(many=True, read_only=True)

    likes_count = serializers.IntegerField(read_only=True)
    comments_count = serializers.IntegerField(read_only=True)
    is_liked = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            'id', 'author', 'content_text', 'location_point', 'privacy',
            'is_edited', 'is_deleted', 'created_at', 'updated_at',
            'media', 'hashtags', 'mentions',
            'likes_count', 'comments_count', 'is_liked',
        ]

    def get_is_liked(self, obj) -> bool:
        """
        Returns True if the current user has liked this post.
        Uses prefetched `user_reactions` list — avoids any extra DB query.
        """
        user_reactions = getattr(obj, 'user_reactions', None)
        if user_reactions is not None:
            return len(user_reactions) > 0
        # Fallback if Prefetch was not applied
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return obj.likes.filter(user=request.user).exists()
        return False


class PostDetailSerializer(PostListSerializer):
    """
    Single post detail view with prefetched recent top-level comments.
    """
    recent_comments = serializers.SerializerMethodField()

    class Meta(PostListSerializer.Meta):
        fields = PostListSerializer.Meta.fields + ['recent_comments']

    def get_recent_comments(self, obj):
        # `?comments=0`: the app loads comments separately (paginated, from
        # /post/comments/?post_id=…), so the post itself comes back fast.
        request = self.context.get('request')
        if request is not None and request.query_params.get('comments') == '0':
            return []
        # ViewSet sets `recent_comments_qs` via Prefetch — no extra DB hit
        if hasattr(obj, 'recent_comments_qs'):
            comments = obj.recent_comments_qs
        else:
            comments = obj.comments.filter(
                parent_comment__isnull=True
            ).select_related('user', 'user__profile').order_by('created_at')[:3]
        return PostCommentSerializer(comments, many=True).data


class SavedPostSerializer(serializers.ModelSerializer):
    post = PostListSerializer(read_only=True)

    class Meta:
        model = SavedPost
        fields = ['id', 'post', 'created_at']
