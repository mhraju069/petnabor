"""
Views for managing notification settings, FCM devices, and user notifications.
"""
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.permissions import IsAuthenticated

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets, serializers
from rest_framework.decorators import action
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiParameter, OpenApiTypes
from .paginations import NotificationPagination

from .serializers import (
    NotificationSettingsSerializer,
    FCMDeviceSerializer,
    NotificationSerializer,
)
from .models import NotificationSettings, FCMDevice, Notifications


class NotificationSettingsView(RetrieveUpdateAPIView):
    serializer_class = NotificationSettingsSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        obj, created = NotificationSettings.objects.get_or_create(
            user=self.request.user
        )
        return obj


class FCMDeviceView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses=FCMDeviceSerializer(many=True)
    )
    def get(self, request):
        devices = FCMDevice.objects.filter(user=request.user)
        serializer = FCMDeviceSerializer(devices, many=True)
        return Response(serializer.data)

    @extend_schema(
        request=FCMDeviceSerializer,
        responses=FCMDeviceSerializer
    )
    def post(self, request):
        serializer = FCMDeviceSerializer(data=request.data)

        if serializer.is_valid():
            registration_id = serializer.validated_data["registration_id"]

            obj, created = FCMDevice.objects.update_or_create(
                registration_id=registration_id,
                defaults={
                    "user": request.user,
                    "custom_notifications": serializer.validated_data.get(
                        "custom_notifications", False
                    ),
                },
            )
            if created:
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            else:
                return Response(serializer.data, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        request=inline_serializer(
            name="FCMDeviceDelete",
            fields={"registration_id": serializers.CharField()}
        ),
        responses={204: None}
    )
    def delete(self, request):
        registration_id = request.data.get("registration_id")
        if not registration_id:
            return Response(
                {"error": "registration_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            device = FCMDevice.objects.get(
                registration_id=registration_id, user=request.user
            )
            device.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except FCMDevice.DoesNotExist:
            return Response(
                {"error": "Device not found"}, status=status.HTTP_404_NOT_FOUND
            )


class NotificationViewSet(viewsets.ModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = NotificationPagination

    def get_queryset(self):
        return Notifications.objects.filter(user=self.request.user).order_by(
            "-created_at"
        )

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number for notifications.",
                required=False,
                default=1,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of notifications per page.",
                required=False,
                default=20,
            ),
        ],
        responses=NotificationSerializer(many=True)
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        request=None,
        responses={200: inline_serializer(
            name="MarkAllReadResponse",
            fields={"status": serializers.CharField()}
        )}
    )
    @action(detail=False, methods=["post"])
    def mark_all_read(self, request):
        self.get_queryset().filter(is_read=False).update(is_read=True)
        return Response({"status": "all notifications marked as read"})

    @extend_schema(
        request=None,
        responses={200: inline_serializer(
            name="MarkReadResponse",
            fields={"status": serializers.CharField()}
        )}
    )
    @action(detail=True, methods=["post"])
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        if not notification.is_read:
            notification.is_read = True
            notification.save()
        return Response({"status": "notification marked as read"})
