import json
from django.core.cache import cache
from django.db.models import Count, Prefetch
from django.utils import timezone
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import ChatThread, Message, ThreadParticipant
from . import services

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get("user")
        if not self.user or self.user.is_anonymous:
            await self.close()
            return

        # Derive the base URL (scheme + host) from the WS scope so we can
        # build absolute media URLs identical to request.build_absolute_uri().
        self.base_url = self._get_base_url()

        # Join a personal group to receive notifications intended for this user
        self.user_group = f"user_{self.user.id}"
        await self.channel_layer.group_add(
            self.user_group,
            self.channel_name
        )

        await self.set_user_online(True)
        await self.accept()

    def _get_base_url(self):
        """
        Build 'https://app.petnabor.com' from the WebSocket scope.
        - scheme: 'wss'→'https', 'ws'→'http' (nginx sets X-Forwarded-Proto)
        - host:   pulled from the HTTP Host header in the handshake
        """
        scope = self.scope
        # WebSocket type is 'websocket'; use forwarded proto if available.
        headers = dict(scope.get("headers", []))
        scheme = headers.get(b"x-forwarded-proto", b"").decode() or (
            "https" if scope.get("type") == "websocket" and scope.get("scheme") == "wss"
            else "http"
        )
        host = headers.get(b"host", b"localhost").decode()
        return f"{scheme}://{host}"

    async def disconnect(self, close_code):
        if hasattr(self, "user_group"):
            await self.set_user_online(False)
            await self.channel_layer.group_discard(
                self.user_group,
                self.channel_name
            )

    @database_sync_to_async
    def set_user_online(self, is_online):
        self.user.is_online = is_online
        self.user.last_active = timezone.now()
        self.user.save(update_fields=["is_online", "last_active"])

    async def receive(self, text_data):
        """
        Handle incoming WebSocket messages from client.
        Currently used for real-time pulses, e.g., 'marking_as_read'.
        """
        try:
            data = json.loads(text_data)
            action = data.get("action")
            
            if action == "mark_read":
                thread_id = data.get("thread_id")
                if thread_id:
                    await self.handle_mark_read(thread_id)
                    
        except Exception:
            pass

    async def handle_mark_read(self, thread_id):
        # Delegate to service (wrapped in sync_to_async)
        await database_sync_to_async(services.mark_messages_read)(self.user, thread_id)
        
        # Broadcast the 'read' event to other participants in the thread
        # Note: In a production app with 1M users, we avoid putting 1M users 
        # in one group. Instead, we pull participants and notify their personal groups.
        participants = await self.get_thread_participants(thread_id)
        for p_id in participants:
            if str(p_id) != str(self.user.id):
                await self.channel_layer.group_send(
                    f"user_{p_id}",
                    {
                        "type": "message_read",
                        "thread_id": thread_id,
                        "reader_id": str(self.user.id)
                    }
                )

    @database_sync_to_async
    def get_thread_participants(self, thread_id):
        return list(
            ThreadParticipant.objects.filter(thread_id=thread_id, left_at__isnull=True)
            .values_list("user_id", flat=True)
            .distinct()
        )

    # ──────────────────────────────────────────────
    # DB helpers
    # ──────────────────────────────────────────────

    @database_sync_to_async
    def get_top_threads(self):
        """
        Return the top-20 threads for the connected user, ordered by
        last_message_timestamp desc, as a JSON-safe list of plain dicts.

        Optimisations applied:
        - Short-lived Redis cache (10 s) — burst messages hit cache, not DB.
        - No messages prefetch — last_message_text is already denormalised on
          ChatThread, so fetching raw Message rows is unnecessary.
        - only() limits the columns SELECTed for both ChatThread and User.
        - Participant prefetch filters to active members (left_at IS NULL).
        - Online status computed inline from last_active to avoid an extra
          property call that could trigger attribute resolution.
        """
        MAX_THREADS = 20
        CACHE_TTL   = 10   # seconds — short enough to stay fresh
        me_id       = self.user.id

        cache_key = f"ws_threads_{me_id}"
        cached    = cache.get(cache_key)
        if cached is not None:
            return cached

        # ── 1. Thread IDs the user belongs to ────────────────────────────
        active_thread_ids = list(
            ThreadParticipant.objects
            .filter(user_id=me_id, left_at__isnull=True)
            .values_list("thread_id", flat=True)
        )

        # ── 2. Threads + active-participant profiles (2 SQL queries total) ─
        threads = list(
            ChatThread.objects
            .filter(id__in=active_thread_ids)
            .only(
                "id", "thread_type", "name", "avatar_url",
                "last_message_text", "last_message_timestamp", "created_at",
            )
            .prefetch_related(
                Prefetch(
                    "participants",
                    queryset=(
                        ThreadParticipant.objects
                        .filter(left_at__isnull=True)           # active only
                        .select_related("user__profile")
                        .only(
                            "id", "thread_id", "user_id", "left_at", "cleared_history_at",
                            "user__id", "user__username",
                            "user__first_name", "user__last_name",
                            "user__last_active",
                            "user__profile__profile_picture",
                        )
                    ),
                    to_attr="active_participants",
                )
            )
            .order_by("-last_message_timestamp")[:MAX_THREADS]
        )

        # ── 3. Snapshot "now" once; reuse for every online-status check ───
        now = timezone.now()

        # ── 4. Bulk-fetch unread counts for all threads in one query ─────
        #    {thread_id: unread_count}
        unread_map = {}
        if active_thread_ids:
            rows = (
                Message.objects
                .filter(
                    thread_id__in=active_thread_ids,
                    is_read=False,
                    is_deleted_for_everyone=False,
                )
                .exclude(sender_id=me_id)
                .values("thread_id")
                .annotate(cnt=Count("id"))
            )
            unread_map = {str(row["thread_id"]): row["cnt"] for row in rows}

        def _is_online(last_active):
            return bool(last_active and (now - last_active).total_seconds() < 300)

        base_url = getattr(self, "base_url", "")

        def _avatar(participant):
            profile = getattr(participant.user, "profile", None)
            if profile and profile.profile_picture:
                try:
                    relative = profile.profile_picture.url  # e.g. /media/avatars/x.jpg
                    # Already absolute (S3 / CloudFront URLs start with http)
                    if relative.startswith("http"):
                        return relative
                    return f"{base_url}{relative}"
                except Exception:
                    pass
            return None

        def _user_dict(participant):
            u = participant.user
            return {
                "id":         str(u.id),
                "username":   u.username,
                "first_name": u.first_name,
                "last_name":  u.last_name,
                "avatar":     _avatar(participant),
                "is_online":  _is_online(u.last_active),
                "last_seen":  u.last_active.isoformat() if u.last_active else None,
            }

        # ── 4. Serialise to plain dicts ───────────────────────────────────
        result = []
        for thread in threads:
            parts = getattr(thread, "active_participants", [])

            other_user = None
            my_cleared_at = None
            if thread.thread_type == "DIRECT":
                for p in parts:
                    if p.user_id != me_id:
                        other_user = _user_dict(p)
                    else:
                        my_cleared_at = p.cleared_history_at

            members = None
            if thread.thread_type == "GROUP":
                members = []
                for p in parts:
                    members.append(_user_dict(p))
                    if p.user_id == me_id:
                        my_cleared_at = p.cleared_history_at

            # Hide message for THIS user if they cleared their chat after it was sent.
            last_text = thread.last_message_text
            last_timestamp = thread.last_message_timestamp
            
            if my_cleared_at and last_timestamp and last_timestamp <= my_cleared_at:
                last_text = None
                last_timestamp = None

            result.append({
                "id":                      str(thread.id),
                "thread_type":             thread.thread_type,
                "name":                    thread.name,
                "avatar_url":              thread.avatar_url,
                "other_user":              other_user,
                "members":                 members,
                "last_message_text":       last_text,
                "last_message_timestamp":  (
                    last_timestamp.isoformat()
                    if last_timestamp else None
                ),
                "is_read":                 unread_map.get(str(thread.id), 0) == 0,
                "created_at":              thread.created_at.isoformat(),
            })

        cache.set(cache_key, result, timeout=CACHE_TTL)
        return result

    # ──────────────────────────────────────────────
    # Group message handlers
    # ──────────────────────────────────────────────

    async def chat_message(self, event):
        """Receive new message from group and send to client."""
        await self.send(text_data=json.dumps({
            "type": "new_message",
            "message": event["message"]
        }))

    async def message_read(self, event):
        """Notify client that a message was read."""
        await self.send(text_data=json.dumps({
            "type": "message_read",
            "thread_id": event["thread_id"],
            "reader_id": event["reader_id"]
        }))

    async def thread_list_update(self, event):
        """
        Triggered when a new message is sent to any thread for this user.
        Fetches the current user's top-20 thread list from the DB and
        pushes it to the client so the frontend can re-render the inbox
        with the active thread moved to the top.
        """
        threads = await self.get_top_threads()
        await self.send(text_data=json.dumps({
            "type": "thread_list_update",
            "threads": threads,
        }))
