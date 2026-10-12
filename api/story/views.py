"""
ViewSets for the Story feature.

All business logic lives in the service layer.
Views are kept thin — they: authenticate, validate input, call service, return response.

Endpoint map (all under /story/stories/):
  POST   /                     → publish_story
  GET    /                     → list (own active stories)
  GET    /{id}/                → retrieve single story
  DELETE /{id}/                → delete_story
  GET    /feed/                → story feed (friends + self, unseen-first)
  GET    /user_stories/        → public stories of any user (?user_id=<uuid>)
  POST   /{id}/view/           → mark_as_viewed
  GET    /{id}/viewers/        → viewers list (author only)
  POST   /{id}/react/          → react / change reaction
  DELETE /{id}/react/          → remove reaction
  POST   /{id}/reply/          → reply to story
  GET    /{id}/replies/        → list replies (author sees all; others see own)
"""

import logging

from rest_framework import permissions, status, viewsets, serializers
from rest_framework.decorators import action
from rest_framework.pagination import CursorPagination, PageNumberPagination
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiParameter, OpenApiTypes


from .models import Story
from .permissions import CanViewStory, IsStoryAuthor
from .serializers import (
    StoryCreateSerializer,
    StoryDetailSerializer,
    StoryListSerializer,
    StoryReactionCreateSerializer,
    StoryReactionSerializer,
    StoryReplySerializer,
    StoryUserGroupSerializer,
    StoryViewSerializer,
)
from .services import (
    StoryFeedService,
    StoryInteractionService,
    StoryService,
    StoryViewService,
    _annotate_story_queryset,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Pagination
# ──────────────────────────────────────────────


class StoryCursorPagination(CursorPagination):
    page_size = 12
    max_page_size = 50
    ordering = "-created_at"


class StoryGroupedFeedPagination(PageNumberPagination):
    page_size = 20
    max_page_size = 50
    page_size_query_param = "page_size"


class StoryViewCursorPagination(CursorPagination):
    page_size = 20
    max_page_size = 50
    ordering = "-viewed_at"


# ──────────────────────────────────────────────
# Story ViewSet
# ──────────────────────────────────────────────


class StoryViewSet(viewsets.ModelViewSet):
    """
    Central ViewSet covering all story CRUD and interaction endpoints.
    Actions: publish, retrieve, delete, feed, user_stories,
             view, viewers, react, reply, replies.
    """

    permission_classes = [permissions.IsAuthenticated, IsStoryAuthor, CanViewStory]
    pagination_class = StoryCursorPagination
    # Disable PUT/PATCH — stories are immutable once published
    http_method_names = ["get", "post", "delete", "head", "options"]

    # Interaction actions are NOT authorship-restricted —
    # any authenticated user who can VIEW the story may interact with it.
    _INTERACTION_ACTIONS = {"react", "view", "reply"}

    def get_permissions(self):
        if self.action in self._INTERACTION_ACTIONS:
            # Skip IsStoryAuthor — react/view/reply is open to all viewers
            return [permissions.IsAuthenticated(), CanViewStory()]
        return super().get_permissions()

    def get_queryset(self):
        """
        - list  → requesting user's own active stories
        - other → all active stories (permission classes filter further)
        """
        user = self.request.user
        if self.action == "list":
            return _annotate_story_queryset(
                StoryService.get_active_queryset().filter(author=user), user
            )
        return _annotate_story_queryset(StoryService.get_active_queryset(), user)

    def get_serializer_class(self):
        if self.action == "create":
            return StoryCreateSerializer
        if self.action == "retrieve":
            return StoryDetailSerializer
        return StoryListSerializer

    # ── Publish ───────────────────────────────────────────────────────────

    def create(self, request, *args, **kwargs):
        """
        POST /stories/ — publish a new story.

        Honours an `Idempotency-Key` header: a retry of an upload that already
        succeeded returns that story (200) instead of creating a duplicate.
        """
        from api.core.idempotency import idempotent_create

        with idempotent_create(request, "story") as idem:
            if idem.existing_id:
                story = Story.objects.filter(id=idem.existing_id, author=request.user).first()
                if story:
                    return Response(
                        StoryDetailSerializer(story, context={"request": request}).data,
                        status=status.HTTP_200_OK,
                    )
            serializer = StoryCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            story = StoryService.publish_story(
                user=request.user,
                data=serializer.validated_data,
            )
            idem.remember(story.id)
        return Response(
            StoryDetailSerializer(story, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    # ── Delete ────────────────────────────────────────────────────────────

    def destroy(self, request, *args, **kwargs):
        """DELETE /stories/{id}/ — delete own story (hard delete)."""
        story = self.get_object()
        try:
            StoryService.delete_story(story, request.user)
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return Response({"detail": "Story deleted."}, status=status.HTTP_200_OK)

    # ── Feed ──────────────────────────────────────────────────────────────

    @extend_schema(
        parameters=[
            OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="page_size", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, default=20),
        ],
        responses=StoryUserGroupSerializer(many=True)
    )
    @action(detail=False, methods=["get"])
    def feed(self, request):
        """GET /stories/feed/ — stories grouped by author, unseen-first."""
        groups = StoryFeedService.get_grouped_story_feed(request.user)
        paginator = StoryGroupedFeedPagination()
        page = paginator.paginate_queryset(groups, request, view=self)
        if page is not None:
            serializer = StoryUserGroupSerializer(page, many=True, context={"request": request})
            return paginator.get_paginated_response(serializer.data)
        serializer = StoryUserGroupSerializer(groups, many=True, context={"request": request})
        return Response(serializer.data)

    # ── Public user stories ───────────────────────────────────────────────

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="user_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description="User ID whose active stories will be returned.",
                required=True,
            ),
            OpenApiParameter(
                name="cursor",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Cursor token for loading the next page.",
                required=False,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of stories per page.",
                required=False,
                default=12,
            ),
        ],
        responses=StoryListSerializer(many=True)
    )
    @action(detail=False, methods=["get"], url_path="user_stories")
    def user_stories(self, request):
        """
        GET /stories/user_stories/?user_id=<uuid>
        Returns active stories for any user, respecting privacy.
        """
        user_id = request.query_params.get("user_id")
        if not user_id:
            return Response(
                {"detail": "user_id query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from django.contrib.auth import get_user_model
        User = get_user_model()

        try:
            target_user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND
            )

        qs = StoryService.get_active_stories_for_user(target_user, request.user)
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = StoryListSerializer(page, many=True, context={"request": request})
            return self.get_paginated_response(serializer.data)
        serializer = StoryListSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data)

    # ── View / Viewers ────────────────────────────────────────────────────

    @extend_schema(
        request=None,
        responses={
            200: inline_serializer(
                name="StoryViewResponse",
                fields={
                    "status": serializers.CharField(),
                    "views_count": serializers.IntegerField(),
                    "detail": serializers.CharField(required=False)
                }
            )
        }
    )
    @action(detail=True, methods=["post"])
    def view(self, request, pk=None):
        """POST /stories/{id}/view/ — mark story as viewed by the requester."""
        story = self.get_object()
        # Don't count the author viewing their own story
        if story.author_id == request.user.id:
            return Response(
                {"detail": "Authors do not count as viewers of their own stories."},
                status=status.HTTP_200_OK,
            )
        is_new_view = StoryViewService.mark_as_viewed(story, request.user)
        story.refresh_from_db(fields=["views_count"])
        return Response(
            {
                "status": "viewed" if is_new_view else "already_viewed",
                "views_count": story.views_count,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="cursor",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Cursor token for loading the next page of viewers.",
                required=False,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of viewers per page.",
                required=False,
                default=20,
            ),
        ],
        responses=StoryViewSerializer(many=True)
    )
    @action(detail=True, methods=["get"])
    def viewers(self, request, pk=None):
        """GET /stories/{id}/viewers/ — viewers list; only the author may call this."""
        story = self.get_object()
        try:
            qs = StoryViewService.get_story_viewers(story, request.user)
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        paginator = StoryViewCursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        if page is not None:
            serializer = StoryViewSerializer(page, many=True, context={"request": request})
            return paginator.get_paginated_response(serializer.data)
        serializer = StoryViewSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data)

    # ── React ─────────────────────────────────────────────────────────────

    @extend_schema(
        methods=["POST"],
        request=StoryReactionCreateSerializer,
        responses={200: StoryReactionSerializer, 201: StoryReactionSerializer}
    )
    @extend_schema(
        methods=["DELETE"],
        request=None,
        responses={200: inline_serializer('StoryReactionRemoved', {'status': serializers.CharField()})}
    )
    @action(detail=True, methods=["post", "delete"])
    def react(self, request, pk=None):
        """
        POST   /stories/{id}/react/ → react or change reaction
        DELETE /stories/{id}/react/ → remove reaction
        """
        story = self.get_object()

        if request.method == "DELETE":
            removed = StoryInteractionService.remove_reaction(story, request.user)
            if removed:
                return Response({"status": "reaction_removed"}, status=status.HTTP_200_OK)
            return Response(
                {"detail": "No reaction to remove."},
                status=status.HTTP_404_NOT_FOUND,
            )

        input_serializer = StoryReactionCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        reaction, created = StoryInteractionService.react_to_story(
            story=story,
            user=request.user,
            reaction_type=input_serializer.validated_data["reaction_type"],
        )
        return Response(
            StoryReactionSerializer(reaction, context={"request": request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    # ── Reply ─────────────────────────────────────────────────────────────

    @extend_schema(
        methods=["GET"],
        parameters=[
            OpenApiParameter(
                name="cursor",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Cursor token for loading the next page of replies.",
                required=False,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of replies per page.",
                required=False,
                default=12,
            ),
        ],
        responses=StoryReplySerializer(many=True)
    )
    @extend_schema(
        methods=["POST"],
        request=inline_serializer('StoryReplyRequest', {'reply_text': serializers.CharField()}),
        responses={201: StoryReplySerializer}
    )
    @action(detail=True, methods=["post", "get"])
    def reply(self, request, pk=None):
        """
        POST /stories/{id}/reply/ → send a reply to the story
        GET  /stories/{id}/reply/ → list replies (author sees all; others see own)
        """
        story = self.get_object()

        if request.method == "GET":
            qs = StoryInteractionService.get_story_replies(story, request.user)
            page = self.paginate_queryset(qs)
            if page is not None:
                serializer = StoryReplySerializer(page, many=True, context={"request": request})
                return self.get_paginated_response(serializer.data)
            serializer = StoryReplySerializer(qs, many=True, context={"request": request})
            return Response(serializer.data)

        # POST
        serializer = StoryReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reply = StoryInteractionService.reply_to_story(
            story=story,
            user=request.user,
            reply_text=serializer.validated_data["reply_text"],
        )
        return Response(
            StoryReplySerializer(reply, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )
