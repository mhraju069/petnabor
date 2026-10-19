"""
ASGI configuration for the PetNabor project.
"""
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from django.core.asgi import get_asgi_application  # noqa: E402
from django.urls import path  # noqa: E402
from api.messaging.routing import websocket_urlpatterns as chat_websocket_urlpatterns  # noqa: E402
from api.messaging.middleware import JWTAuthMiddleware  # noqa: E402
from api.core.consumers import AppEventsConsumer  # noqa: E402

websocket_urlpatterns = [
    *chat_websocket_urlpatterns,
    # Live "something changed" events (new posts, nearby sitters).
    path("ws/events/", AppEventsConsumer.as_asgi()),
]

application = ProtocolTypeRouter(
    {
        "http": get_asgi_application(),
        "websocket": JWTAuthMiddleware(URLRouter(websocket_urlpatterns)),
    }
)
