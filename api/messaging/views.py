"""
Messaging views.

All business logic is delegated to services.py.
Views handle HTTP plumbing only: auth, serialization, response codes.
"""

import logging

from rest_framework import status, views, serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiParameter, OpenApiTypes

from api.users.throttles import PerUserMessagingThrottle

from django.contrib.auth import get_user_model
from django.core.cache import cache

from .models import MessageTypes
from .paginations import ConversationPagination, MessagePagination
from . import services
from .serializers import (
    ChatThreadSerializer,
    CreateDirectThreadSerializer,
    CreateGroupThreadSerializer,
    MessageSerializer,
    SendMessageSerializer,
    BulkDeleteMessagesSerializer,
)

logger = logging.getLogger(__name__)
User = get_user_model()


# ──────────────────────────────────────────────
# Thread (inbox + creation)
# ──────────────────────────────────────────────


class ThreadListCreateView(views.APIView):
    """
    GET  /messaging/threads/       → user's inbox
    POST /messaging/threads/       → start a DIRECT thread
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id='messaging_thread_list',
        parameters=[
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number for inbox results.",
                required=False,
                default=1,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of threads per page.",
                required=False,
                default=20,
            ),
        ],
        responses=ChatThreadSerializer(many=True)
    )
    def get(self, request):
        # Cache key for first page of inbox
        cache_key = f"user_inbox_{request.user.id}_page_1"
        is_first_page = not request.query_params.get("page") or request.query_params.get("page") == "1"

        if is_first_page:
            cached_data = cache.get(cache_key)
            if cached_data:
                return Response(cached_data)

        threads = services.get_threads_for_user(request.user)

        paginator = ConversationPagination()
        page = paginator.paginate_queryset(threads, request)
        if page is not None:
            serializer = ChatThreadSerializer(page, many=True, context={"request": request})
            data = paginator.get_paginated_response(serializer.data).data
            
            if is_first_page:
                cache.set(cache_key, data, timeout=300)  # 5 min cache
                
            return Response(data)

        serializer = ChatThreadSerializer(threads, many=True, context={"request": request})
        return Response(serializer.data)

    @extend_schema(
        request=CreateDirectThreadSerializer,
        responses=ChatThreadSerializer
    )
    def post(self, request):
        """Start a DIRECT conversation with another user."""
        input_serializer = CreateDirectThreadSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        recipient_id = input_serializer.validated_data["recipient_id"]

        try:
            recipient = User.objects.select_related("profile").get(
                id=recipient_id, is_active=True
            )
        except User.DoesNotExist:
            return Response(
                {"success": False, "message": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not services.can_message(request.user, recipient):
            return Response(
                {
                    "success": False,
                    "message": "You can only message users who are your friends.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        thread, created = services.get_or_create_direct_thread(request.user, recipient)
        serializer = ChatThreadSerializer(thread, context={"request": request})
        http_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(serializer.data, status=http_status)


class GroupThreadCreateView(views.APIView):
    """POST /messaging/threads/group/ — create a GROUP thread."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=CreateGroupThreadSerializer,
        responses=ChatThreadSerializer
    )
    def post(self, request):
        input_serializer = CreateGroupThreadSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data

        thread = services.create_group_thread(
            creator=request.user,
            name=data["name"],
            description=data.get("description"),
            avatar_url=data.get("avatar_url"),
            member_ids=data.get("member_ids", []),
        )
        serializer = ChatThreadSerializer(thread, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ThreadDetailView(views.APIView):
    """GET /messaging/threads/<thread_id>/ — retrieve a single thread."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id='messaging_thread_retrieve',
        responses=ChatThreadSerializer
    )
    def get(self, request, thread_id):
        thread, _ = services.get_thread_for_participant(request.user, thread_id)
        serializer = ChatThreadSerializer(thread, context={"request": request})
        return Response(serializer.data)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="everyone",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                description="Set true to delete the thread for all participants (admin only).",
                required=False,
                default=False,
            )
        ],
        responses={
            204: None,
            200: inline_serializer(
                name="LeaveThreadResponse",
                fields={"success": serializers.BooleanField(), "message": serializers.CharField()}
            )
        }
    )
    def delete(self, request, thread_id):
        """
        DELETE /messaging/threads/<id>/
        - If query param `everyone=true`, try to delete thread for all (Admin only).
        - Otherwise, soft-leave thread for the current user.
        """
        everyone = request.query_params.get("everyone") == "true"
        
        if everyone:
            services.delete_thread_for_everyone(request.user, thread_id)
            return Response(status=status.HTTP_204_NO_CONTENT)
        else:
            services.leave_thread(request.user, thread_id)
            return Response(
                {"success": True, "message": "You left the thread."},
                status=status.HTTP_200_OK,
            )


# ──────────────────────────────────────────────
# Messages inside a thread
# ──────────────────────────────────────────────


class MessageListCreateView(views.APIView):
    """
    GET  /messaging/threads/<thread_id>/messages/  → message thread (paginated)
    POST /messaging/threads/<thread_id>/messages/  → send a message
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [PerUserMessagingThrottle]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="cursor",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Cursor token for loading the next page of messages.",
                required=False,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of messages per page.",
                required=False,
                default=30,
            ),
        ],
        responses=MessageSerializer(many=True)
    )
    def get(self, request, thread_id):
        messages = services.get_messages_in_thread(request.user, thread_id)

        # Mark as read (update query — no N+1)
        try:
            services.mark_messages_read(request.user, thread_id)
        except Exception:
            logger.exception("Failed to mark messages as read for thread %s", thread_id)

        paginator = MessagePagination()
        page = paginator.paginate_queryset(messages, request)
        if page is not None:
            serializer = MessageSerializer(page, many=True, context={"request": request})
            return paginator.get_paginated_response(serializer.data)

        serializer = MessageSerializer(messages, many=True, context={"request": request})
        return Response(serializer.data)

    @extend_schema(
        request=SendMessageSerializer,
        responses=MessageSerializer
    )
    def post(self, request, thread_id):
        input_serializer = SendMessageSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data

        message = services.send_message(
            sender=request.user,
            thread_id=thread_id,
            text_content=data.get("text_content"),
            message_type=data.get("message_type", MessageTypes.TEXT),
            media_url=data.get("media_url"),
            media_file=data.get("media"),
            reply_to_id=data.get("reply_to_id"),
        )
        serializer = MessageSerializer(message, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class MessageDetailView(views.APIView):
    """DELETE /messaging/threads/<thread_id>/messages/<message_id>/ — delete for everyone."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={204: None})
    def delete(self, request, thread_id, message_id):
        services.delete_message_for_everyone(request.user, thread_id, message_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class BulkDeleteMessagesView(views.APIView):
    """POST /messaging/threads/<thread_id>/messages/bulk-delete/ — delete own messages."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=BulkDeleteMessagesSerializer,
        responses={200: inline_serializer(
            name="BulkDeleteResponse",
            fields={"success": serializers.BooleanField(), "deleted_count": serializers.IntegerField()}
        )}
    )
    def post(self, request, thread_id):
        input_serializer = BulkDeleteMessagesSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        message_ids = input_serializer.validated_data["message_ids"]

        count = services.delete_messages_bulk(request.user, thread_id, message_ids)
        return Response({"success": True, "deleted_count": count})


class ThreadMuteView(views.APIView):
    """
    POST   /messaging/threads/<thread_id>/mute/ — mute notifications for me
    DELETE /messaging/threads/<thread_id>/mute/ — unmute
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=None,
        responses={200: inline_serializer(
            name="ThreadMuteResponse",
            fields={"is_muted": serializers.BooleanField()},
        )},
    )
    def post(self, request, thread_id):
        services.set_thread_muted(request.user, thread_id, True)
        return Response({"is_muted": True})

    @extend_schema(
        request=None,
        responses={200: inline_serializer(
            name="ThreadUnmuteResponse",
            fields={"is_muted": serializers.BooleanField()},
        )},
    )
    def delete(self, request, thread_id):
        services.set_thread_muted(request.user, thread_id, False)
        return Response({"is_muted": False})


class ClearThreadHistoryView(views.APIView):
    """POST /messaging/threads/<thread_id>/clear-history/ — per-user clear history."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={200: inline_serializer(
            name="ClearHistoryResponse",
            fields={"success": serializers.BooleanField(), "message": serializers.CharField()}
        )}
    )
    def post(self, request, thread_id):
        services.clear_thread_history(request.user, thread_id)
        return Response({"success": True, "message": "History cleared."})
