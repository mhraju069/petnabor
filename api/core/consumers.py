"""
/ws/events/?token=… — the app's live event channel (api/core/realtime.py).

One per open app (the app connects in the foreground, disconnects in the
background). It only delivers small {"type": "<kind>"} events; the app
fetches what changed itself. Separate from the chat socket, which manages
online status and chat payloads.
"""

import json

from channels.generic.websocket import AsyncWebsocketConsumer

from .realtime import BROADCAST_GROUP, user_group


class AppEventsConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if not user or user.is_anonymous:
            await self.close()
            return
        self.groups_joined = [user_group(user.id), BROADCAST_GROUP]
        for group in self.groups_joined:
            await self.channel_layer.group_add(group, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        for group in getattr(self, "groups_joined", []):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        # Nothing to receive; app-level pings are answered so a client can
        # check the connection is alive.
        if text_data and '"ping"' in text_data:
            await self.send(text_data=json.dumps({"type": "pong"}))

    async def app_event(self, event):
        await self.send(text_data=json.dumps({"type": event["kind"]}))
