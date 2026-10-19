"""
Live "something changed" events for the app, over the /ws/events/ socket
(api/core/consumers.py) — instead of the app polling.

Events carry no data, only a kind ("feed.new_post", "sitters.new"). The app
reacts with one request to the matching endpoint (e.g. the new-count one),
which applies privacy, blocks and distance. So server work follows actual
activity, not the number of people with the app open.

    notify_users(user_ids, "feed.new_post")   # specific people
    broadcast("feed.new_post")                # everyone connected, batched

Never raises: a missed event only means the app catches up on its next
foreground/reconnect.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache

logger = logging.getLogger(__name__)

BROADCAST_GROUP = "events_all"
# At most one broadcast per kind per this many seconds; a burst of posts
# becomes one event at the start and one at the end of the window.
BROADCAST_WINDOW_S = {"feed.new_post": 20, "sitters.new": 60}
DEFAULT_WINDOW_S = 30


def user_group(user_id) -> str:
    return f"events_user_{user_id}"


def _send(group: str, kind: str) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(group, {"type": "app.event", "kind": kind})


def notify_users(user_ids, kind: str) -> None:
    for user_id in {str(u) for u in user_ids if u}:
        try:
            _send(user_group(user_id), kind)
        except Exception:
            logger.exception("[realtime] notify %s failed for %s", kind, user_id)


def send_broadcast_now(kind: str) -> None:
    try:
        _send(BROADCAST_GROUP, kind)
    except Exception:
        logger.exception("[realtime] broadcast %s failed", kind)


def broadcast(kind: str) -> None:
    """
    Tell every connected app, batched: the first event in a window goes out
    at once; any more in that window are folded into one sent when it ends
    (so the last post of a burst isn't missed).
    """
    window = BROADCAST_WINDOW_S.get(kind, DEFAULT_WINDOW_S)
    if cache.add(f"rt:window:{kind}", 1, window):
        send_broadcast_now(kind)
        return
    if cache.add(f"rt:pending:{kind}", 1, window):
        from api.notifications.tasks import send_broadcast_event

        send_broadcast_event.apply_async((kind,), countdown=window)
