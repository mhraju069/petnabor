"""
ViewSets for Posts, Comments, Likes, and Saved Posts.
Key improvements:
- Throttle scopes on like/comment/save actions (anti-spam)
- Proper permission classes instead of manual ownership checks in views
- Soft-delete instead of hard-delete on posts
- N+1 fix on replies endpoint (select_related added)
- FeedCursorPagination has max_page_size guard
- get_queryset filters is_deleted=False
"""

import logging

from rest_framework import filters, generics, permissions, status, viewsets, serializers
from rest_framework.decorators import action
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    inline_serializer,
    OpenApiParameter,
    OpenApiTypes,
)


from django.db.models import Prefetch
from django.shortcuts import get_object_or_404

from .models import Post, PostComment, PostLike, SavedPost
from .permissions import IsAuthorOrReadOnly, IsCommentAuthorOrPostAuthor, CanViewPost
from .serializers import (
    PostCommentSerializer,
    PostDetailSerializer,
    PostListSerializer,
    PostCreateUpdateSerializer,
    PostLikeSerializer,
    SavedPostSerializer,
)
from .services import CommentService, FeedService, LikeService, PostService, SaveService
from api.users.models import User
from api.users.throttles import (
    PerUserPostLikeThrottle,
    PerUserPostSaveThrottle,
    PerUserPostCommentThrottle,
)

logger = logging.getLogger(__name__)


class FeedCursorPagination(CursorPagination):
    """Cursor pagination optimized for feeds — prevents deep pagination abuse."""

    page_size = 20
    max_page_size = 50
    ordering = "-created_at"


# ──────────────────────────────────────────────
# Post ViewSet
# ──────────────────────────────────────────────


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search by post text, hashtag, or mentioned username.",
                required=False,
            ),
            OpenApiParameter(
                name="cursor",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Cursor token for the next page.",
                required=False,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of posts per page.",
                required=False,
                default=20,
            ),
        ]
    )
)
class PostViewSet(viewsets.ModelViewSet):
    """
    CRUD for Posts.
    - create / update use PostCreateUpdateSerializer
    - retrieve uses PostDetailSerializer with recent comments prefetch
    - list / feed use PostListSerializer
    - Soft-delete via destroy
    """

    permission_classes = [permissions.IsAuthenticated, IsAuthorOrReadOnly, CanViewPost]
    pagination_class = FeedCursorPagination
    filter_backends = [filters.SearchFilter]
    search_fields = [
        "content_text",
        "hashtags__name",
        "mentions__username",
    ]

    # Interaction actions (like, save) are NOT authorship-restricted —
    # any authenticated user who can VIEW the post may interact with it.
    _INTERACTION_ACTIONS = {"like", "save_post"}

    def get_permissions(self):
        if self.action in self._INTERACTION_ACTIONS:
            # Skip IsAuthorOrReadOnly — react/save is open to all viewers
            return [permissions.IsAuthenticated(), CanViewPost()]
        return super().get_permissions()

    def get_serializer_class(self):
        if self.action in ["create", "update", "partial_update"]:
            return PostCreateUpdateSerializer
        if self.action == "retrieve":
            return PostDetailSerializer
        return PostListSerializer

    def get_queryset(self):
        """
        - GET /posts/ (list) returns only the requesting user's posts.
        - GET /posts/{id}/ (retrieve) and actions work on any visible post.
        """
        if self.action == "list":
            return PostService.get_user_posts(self.request.user, self.request.user)

        # More inclusive queryset for retrieve and other actions
        qs = Post.objects.filter(is_deleted=False)

        if self.action == "retrieve":
            recent_comments = (
                PostComment.objects.filter(parent_comment__isnull=True)
                .select_related("user", "user__profile")
                .order_by("created_at")
            )

            qs = qs.prefetch_related(
                Prefetch(
                    "comments", queryset=recent_comments, to_attr="recent_comments_qs"
                )
            )

        # Apply basic optimizations found in get_user_posts but for any author
        return qs.select_related("author", "author__profile").prefetch_related(
            "media",
            "hashtags",
            Prefetch("mentions", queryset=User.objects.select_related("profile")),
            Prefetch(
                "likes",
                queryset=PostLike.objects.filter(user=self.request.user),
                to_attr="user_reactions",
            ),
        )

    def create(self, request, *args, **kwargs):
        """
        Handles multipart/form-data post creation via service layer.

        Honours an `Idempotency-Key` header: a retry of an upload that already
        succeeded returns that post (200) instead of creating a duplicate.
        """
        from api.core.idempotency import idempotent_create

        with idempotent_create(request, "post") as idem:
            if idem.existing_id:
                existing = Post.objects.filter(id=idem.existing_id, author=request.user).first()
                if existing:
                    return Response(
                        PostDetailSerializer(existing, context={"request": request}).data,
                        status=status.HTTP_200_OK,
                    )
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            files = request.FILES.getlist("media")
            post = PostService.create_post(
                user=request.user,
                data=serializer.validated_data,
                files=files,
            )
            idem.remember(post.id)

        return Response(
            PostDetailSerializer(post, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    def perform_update(self, serializer):
        serializer.save(is_edited=True)

    def destroy(self, request, *args, **kwargs):
        """Soft-delete instead of hard-delete — requires IsPostAuthor permission."""
        post = self.get_object()
        self.check_object_permissions(request, post)
        PostService.soft_delete_post(post, request.user)
        return Response({"detail": "Post deleted."}, status=status.HTTP_200_OK)

    # ── Feed ──────────────────────────────────────────────────────────

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="cursor",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Cursor token for feed pagination.",
                required=False,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of feed posts per page.",
                required=False,
                default=20,
            ),
        ],
        responses=PostListSerializer(many=True)
    )
    @action(detail=False, methods=["get"])
    def feed(self, request):
        """
        Public discovery feed.
        Returns ALL public posts from every user, plus FRIENDS_ONLY posts from friends.
        All posts sorted newest-first. Blocked users (both directions) are excluded.
        """
        qs = FeedService.get_feed(request.user)
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = PostListSerializer(
                page, many=True, context={"request": request}
            )
            return self.get_paginated_response(serializer.data)
        serializer = PostListSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="since",
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                description="The `now` from the previous call (when the user last opened Explore).",
                required=False,
            ),
        ],
        responses=inline_serializer(
            name="NewCountResponse",
            fields={"count": serializers.IntegerField(), "now": serializers.DateTimeField()},
        ),
    )
    @action(detail=False, methods=["get"], url_path="feed/new-count")
    def feed_new_count(self, request):
        """
        GET /post/posts/feed/new-count/?since=… — feed posts by others since
        then (the Explore tab badge). No `since`: 0, plus `now` to start from.
        """
        from api.core.new_counts import badge_response_data, capped_count, parse_since

        since = parse_since(request)
        count = 0
        if since is not None:
            qs = (
                FeedService.get_feed(request.user)
                .filter(created_at__gt=since)
                .exclude(author=request.user)
                .order_by()
            )
            count = capped_count(qs)
        return Response(badge_response_data(count))

    # ── Like ──────────────────────────────────────────────────────────

    @extend_schema(
        request=inline_serializer(
            name="PostReactionRequest",
            fields={"reaction_type": serializers.CharField(required=False, default="LIKE")}
        ),
        responses={
            200: inline_serializer(
                name="PostReactionResponse",
                fields={
                    "status": serializers.CharField(),
                    "likes_count": serializers.IntegerField(),
                    "id": serializers.UUIDField(required=False),
                    # and other fields from PostLikeSerializer
                }
            )
        }
    )
    @action(detail=True, methods=["post", "delete"])
    def like(self, request, pk=None):
        """
        POST  → like/react to a post
        DELETE → remove like
        Always returns the updated likes_count for easy frontend use.
        """
        self.throttle_classes = [PerUserPostLikeThrottle]
        post = self.get_object()

        if request.method == "DELETE":
            LikeService.toggle_like(post, request.user)
            post.refresh_from_db(fields=["likes_count"])
            return Response(
                {"status": "unliked", "likes_count": post.likes_count},
                status=status.HTTP_200_OK,
            )

        reaction = request.data.get("reaction_type", "LIKE")
        like_obj, created = LikeService.toggle_like(post, request.user, reaction)
        post.refresh_from_db(fields=["likes_count"])

        data = (
            PostLikeSerializer(like_obj, context={"request": request}).data
            if like_obj
            else {}
        )
        data["likes_count"] = post.likes_count
        data["status"] = "liked" if like_obj else "unliked"

        return Response(
            data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )

    # ── Save ──────────────────────────────────────────────────────────

    @extend_schema(
        request=None,
        responses={
            200: inline_serializer(
                name="PostSaveResponse",
                fields={"status": serializers.CharField()}
            )
        }
    )
    @action(detail=True, methods=["post", "delete"])
    def save_post(self, request, pk=None):
        """POST → save; DELETE → unsave."""
        self.throttle_classes = [PerUserPostSaveThrottle]
        post = self.get_object()
        saved = SaveService.toggle_save(post, request.user)
        if saved:
            return Response({"status": "saved"}, status=status.HTTP_201_CREATED)
        return Response({"status": "unsaved"}, status=status.HTTP_200_OK)


# ──────────────────────────────────────────────
# Comment ViewSet
# ──────────────────────────────────────────────


class PostCommentViewSet(viewsets.ModelViewSet):
    """
    CRUD for top-level comments.
    - Throttled creation to prevent spam (per user, not per IP).
    - Destruction allowed by comment author OR post author (via permission class).
    """

    serializer_class = PostCommentSerializer
    permission_classes = [permissions.IsAuthenticated, IsCommentAuthorOrPostAuthor]
    pagination_class = FeedCursorPagination
    throttle_classes = [PerUserPostCommentThrottle]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="post_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description="Filter comments for a specific post.",
                required=False,
            ),
            OpenApiParameter(
                name="cursor",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Cursor token for the next page.",
                required=False,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of comments per page.",
                required=False,
                default=20,
            ),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        post_id = self.request.query_params.get("post_id")
        qs = PostComment.objects.select_related("user", "user__profile").filter(
            parent_comment__isnull=True
        )
        if post_id:
            qs = qs.filter(post_id=post_id)
        return qs

    def perform_create(self, serializer):
        instance = CommentService.create_comment(
            user=self.request.user,
            validated_data=serializer.validated_data,
        )
        serializer.instance = instance

    def perform_update(self, serializer):
        serializer.save(is_edited=True)

    def perform_destroy(self, instance):
        """Permission class handles ownership check — just call the service."""
        CommentService.delete_comment(instance)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="cursor",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Cursor token for replies pagination.",
                required=False,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of replies per page.",
                required=False,
                default=20,
            ),
        ],
        responses=PostCommentSerializer(many=True)
    )
    @action(detail=True, methods=["get"])
    def replies(self, request, pk=None):
        """Paginated replies for a specific parent comment — select_related added."""
        parent_comment = self.get_object()
        qs = parent_comment.replies.select_related("user", "user__profile").all()
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)


# ──────────────────────────────────────────────
# Saved Posts ViewSet
# ──────────────────────────────────────────────


class SavedPostViewSet(viewsets.ReadOnlyModelViewSet):
    """Returns the authenticated user's bookmarked posts."""

    serializer_class = SavedPostSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = FeedCursorPagination

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="cursor",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Cursor token for the next page.",
                required=False,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of saved posts per page.",
                required=False,
                default=20,
            ),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        return (
            SavedPost.objects.select_related(
                "post",
                "post__author",
                "post__author__profile",
            )
            .prefetch_related(
                "post__media",
                "post__hashtags",
                Prefetch(
                    "post__mentions", queryset=User.objects.select_related("profile")
                ),
            )
            .filter(user=self.request.user)
        )


class UserPostListView(generics.ListAPIView):
    """
    GET /post/user/<uuid:user_id>/posts/
    Public (authenticated) — returns any user's posts.
    Privacy is enforced by PostService.get_user_posts (respects blocks, visibility).
    """

    serializer_class = PostListSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = FeedCursorPagination

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="cursor",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Cursor token for the next page.",
                required=False,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of posts per page.",
                required=False,
                default=20,
            ),
        ]
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def get_queryset(self):
        user_id = self.kwargs["user_id"]
        target_user = get_object_or_404(User, id=user_id, is_active=True)
        return PostService.get_user_posts(target_user, self.request.user)
