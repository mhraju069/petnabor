"""
Messaging URL configuration.

Endpoints:
  GET  /messaging/threads/                              → inbox
  POST /messaging/threads/                              → start a DIRECT thread
  POST /messaging/threads/group/                        → create GROUP thread
  GET  /messaging/threads/<thread_id>/                  → thread detail
  POST /messaging/threads/<thread_id>/clear-history/    → clear personal history
  GET  /messaging/threads/<thread_id>/messages/         → message list
  POST /messaging/threads/<thread_id>/messages/         → send message
  DEL  /messaging/threads/<thread_id>/messages/<id>/    → delete message for everyone
"""

from django.urls import path

from .views import (
    BulkDeleteMessagesView,
    ClearThreadHistoryView,
    ThreadMuteView,
    GroupThreadCreateView,
    MessageDetailView,
    MessageListCreateView,
    ThreadDetailView,
    ThreadListCreateView,
)

urlpatterns = [
    # Thread endpoints
    path(
        "threads/",
        ThreadListCreateView.as_view(),
        name="thread-list-create",
    ),
    path(
        "threads/group/",
        GroupThreadCreateView.as_view(),
        name="group-thread-create",
    ),
    path(
        "threads/<uuid:thread_id>/",
        ThreadDetailView.as_view(),
        name="thread-detail",
    ),
    path(
        "threads/<uuid:thread_id>/mute/",
        ThreadMuteView.as_view(),
        name="thread-mute",
    ),
    path(
        "threads/<uuid:thread_id>/clear-history/",
        ClearThreadHistoryView.as_view(),
        name="thread-clear-history",
    ),
    # Message endpoints
    path(
        "threads/<uuid:thread_id>/messages/",
        MessageListCreateView.as_view(),
        name="message-list-create",
    ),
    path(
        "threads/<uuid:thread_id>/messages/bulk-delete/",
        BulkDeleteMessagesView.as_view(),
        name="message-bulk-delete",
    ),
    path(
        "threads/<uuid:thread_id>/messages/<uuid:message_id>/",
        MessageDetailView.as_view(),
        name="message-detail",
    ),
]
