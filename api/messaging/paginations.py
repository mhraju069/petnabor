"""
Messaging pagination classes.

- ConversationPagination: page-number based (inbox list)
- MessagePagination: cursor-based (message thread — most scalable for large threads)
"""

from rest_framework.pagination import CursorPagination, PageNumberPagination


class ConversationPagination(PageNumberPagination):
    """Inbox list pagination — simple page-number style."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 50


class MessagePagination(CursorPagination):
    """
    Cursor-based pagination for message threads.

    Ordered by newest first so the client loads the latest messages first
    and scrolls up to fetch older ones. At 1M+ messages this avoids
    expensive OFFSET queries.
    """

    page_size = 30
    # Documented on the endpoint (OpenAPI) — honour it, within a cap.
    page_size_query_param = "page_size"
    max_page_size = 100
    ordering = "-created_at"
    cursor_query_param = "cursor"
