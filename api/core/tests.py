import os
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.core.mail import send_mail
from api.notifications.email_service import EmailService

class InfrastructureConfigTests(TestCase):
    
    @override_settings(
        USE_S3=True, 
        AWS_S3_CUSTOM_DOMAIN="cdn.petnabor.test",
        STATICFILES_STORAGE="api.core.storage_backends.StaticStorage",
        DEFAULT_FILE_STORAGE="api.core.storage_backends.PublicMediaStorage"
    )
    def test_s3_storage_settings_logic(self):
        """
        Verify that we can override settings to simulate S3 being enabled and 
        that the storage classes can be successfully imported by Django.
        """
        from django.core.files.storage import default_storage
        from django.contrib.staticfiles.storage import staticfiles_storage
        # Merely importing or referring to them tests if Django can resolve the module path
        self.assertIsNotNone(default_storage)
        self.assertIsNotNone(staticfiles_storage)
        
    @override_settings(EMAIL_BACKEND="django_ses.SESBackend")
    @patch("api.notifications.email_service.send_mail")
    def test_email_service_uses_configured_backend(self, mock_send_mail):
        """
        Verify that EmailService correctly calls Django's send_mail, 
        which will delegate to the configured AWS SES backend in production.
        """
        mock_send_mail.return_value = 1 # 1 message sent
        
        result = EmailService.send_html_email(
            subject="Test Subject",
            html_content="<p>Test</p>",
            recipient_list=["test@example.com"]
        )
        
        self.assertTrue(result)
        mock_send_mail.assert_called_once()
        args, kwargs = mock_send_mail.call_args
        self.assertEqual(kwargs['subject'], "Test Subject")
        self.assertEqual(kwargs['recipient_list'], ["test@example.com"])
        
    @patch("api.notifications.email_service.send_mail")
    def test_email_service_handles_failures(self, mock_send_mail):
        """
        Verify EmailService gracefully handles and logs delivery failures 
        (e.g., if AWS SES rejects the message).
        """
        mock_send_mail.side_effect = Exception("AWS SES Delivery Failed")
        
        result = EmailService.send_html_email(
            subject="Test Failure",
            html_content="<p>Fail</p>",
            recipient_list=["fail@example.com"]
        )
        
        self.assertFalse(result)
        mock_send_mail.assert_called_once()


# Tests run on local storage (settings.TESTING), so Cloudinary isn't
# configured; give the backend dummy credentials (no network: destroy is mocked).
@override_settings(CLOUDINARY_STORAGE={"CLOUD_NAME": "test", "API_KEY": "key", "API_SECRET": "secret"})
class AutoMediaCloudinaryStorageDeleteTests(TestCase):
    """
    Stored names carry the file extension, Cloudinary public_ids for images
    and videos don't. delete() must strip it or Cloudinary says "not found"
    and replaced originals are never removed (duplicate media).
    """

    def _storage(self):
        from api.core.storage_backends import AutoMediaCloudinaryStorage

        return AutoMediaCloudinaryStorage()

    def test_image_delete_uses_public_id_without_extension(self):
        from unittest import mock

        with mock.patch("cloudinary.uploader.destroy", return_value={"result": "ok"}) as destroy:
            self.assertTrue(self._storage().delete("media/cloudinary/stories/media/abc_x1.jpg"))
        destroy.assert_called_once_with(
            "media/cloudinary/stories/media/abc_x1", invalidate=True, resource_type="image"
        )

    def test_video_delete_uses_public_id_without_extension(self):
        from unittest import mock

        with mock.patch("cloudinary.uploader.destroy", return_value={"result": "ok"}) as destroy:
            self._storage().delete("media/cloudinary/stories/media/clip_x1.mp4")
        destroy.assert_called_once_with(
            "media/cloudinary/stories/media/clip_x1", invalidate=True, resource_type="video"
        )

    def test_raw_and_extensionless_names_are_passed_through(self):
        from unittest import mock

        with mock.patch("cloudinary.uploader.destroy", return_value={"result": "ok"}) as destroy:
            storage = self._storage()
            storage.delete("media/cloudinary/docs/file.pdf")
            storage.delete("media/cloudinary/legacy/abc")
        self.assertEqual(destroy.call_args_list[0].args[0], "media/cloudinary/docs/file.pdf")
        self.assertEqual(destroy.call_args_list[0].kwargs["resource_type"], "raw")
        self.assertEqual(destroy.call_args_list[1].args[0], "media/cloudinary/legacy/abc")
        self.assertEqual(destroy.call_args_list[1].kwargs["resource_type"], "image")


class RealtimeEventsTests(TestCase):
    """Live "something changed" events (api/core/realtime.py)."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.core.cache import cache

        from api.friends.models import Friendship

        cache.clear()
        User = get_user_model()
        self.author = User.objects.create_user(email="rt_a@test.com", username="rt_a", password="pw")
        self.friend = User.objects.create_user(email="rt_f@test.com", username="rt_f", password="pw")
        self.stranger = User.objects.create_user(email="rt_s@test.com", username="rt_s", password="pw")
        Friendship.objects.create(sender=self.author, receiver=self.friend)

    def _announce(self, privacy):
        from api.post.models import Post
        from api.post.tasks import announce_new_post

        post = Post.objects.create(author=self.author, content_text="hi", privacy=privacy)
        with patch("api.core.realtime.notify_users") as notify, patch("api.core.realtime.broadcast") as bcast:
            announce_new_post(str(post.pk))
        return notify, bcast

    def test_public_post_tells_friends_and_everyone(self):
        notify, bcast = self._announce("PUBLIC")
        self.assertEqual(set(notify.call_args.args[0]), {self.friend.id})
        bcast.assert_called_once_with("feed.new_post")

    def test_friends_only_post_tells_only_friends(self):
        notify, bcast = self._announce("FRIENDS_ONLY")
        self.assertEqual(set(notify.call_args.args[0]), {self.friend.id})
        bcast.assert_not_called()

    def test_private_post_tells_nobody(self):
        notify, bcast = self._announce("PRIVATE")
        notify.assert_not_called()
        bcast.assert_not_called()

    def test_broadcasts_are_batched(self):
        from api.core import realtime

        with patch.object(realtime, "send_broadcast_now") as now, patch(
            "api.notifications.tasks.send_broadcast_event.apply_async"
        ) as later:
            for _ in range(5):
                realtime.broadcast("feed.new_post")
        # First one right away, the rest folded into one at the window's end.
        now.assert_called_once_with("feed.new_post")
        later.assert_called_once()
        self.assertEqual(later.call_args.kwargs["countdown"], realtime.BROADCAST_WINDOW_S["feed.new_post"])


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class AppEventsConsumerTests(TestCase):
    """The /ws/events/ socket delivers events to the right users."""

    def test_user_and_broadcast_events_arrive(self):
        from asgiref.sync import async_to_sync
        from channels.testing import WebsocketCommunicator
        from django.contrib.auth import get_user_model

        from api.core import realtime
        from api.core.consumers import AppEventsConsumer

        user = get_user_model().objects.create_user(email="ws@test.com", username="ws", password="pw")

        async def run():
            communicator = WebsocketCommunicator(AppEventsConsumer.as_asgi(), "/ws/events/")
            communicator.scope["user"] = user
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            from channels.layers import get_channel_layer

            layer = get_channel_layer()
            await layer.group_send(realtime.user_group(user.id), {"type": "app.event", "kind": "feed.new_post"})
            self.assertEqual(await communicator.receive_json_from(), {"type": "feed.new_post"})

            await layer.group_send(realtime.BROADCAST_GROUP, {"type": "app.event", "kind": "sitters.new"})
            self.assertEqual(await communicator.receive_json_from(), {"type": "sitters.new"})
            await communicator.disconnect()

        async_to_sync(run)()

    def test_anonymous_is_rejected(self):
        from asgiref.sync import async_to_sync
        from channels.testing import WebsocketCommunicator
        from django.contrib.auth.models import AnonymousUser

        from api.core.consumers import AppEventsConsumer

        async def run():
            communicator = WebsocketCommunicator(AppEventsConsumer.as_asgi(), "/ws/events/")
            communicator.scope["user"] = AnonymousUser()
            connected, _ = await communicator.connect()
            self.assertFalse(connected)

        async_to_sync(run)()


class SitterEventsTests(TestCase):
    """A sitter becoming findable nearby tells connected apps."""

    def _user(self, name, user_type):
        from django.contrib.auth import get_user_model

        return get_user_model().objects.create_user(
            email=f"{name}@test.com", username=name, password="pw", user_type=user_type
        )

    def _set_location(self, user):
        from django.contrib.gis.geos import Point

        from api.users.models import Profile

        profile, _ = Profile.objects.get_or_create(user=user)
        with patch("api.core.realtime.broadcast") as bcast, self.captureOnCommitCallbacks(execute=True):
            profile.location_point = Point(1.0, 1.0)
            profile.save()
        return bcast

    def test_sitter_location_announces(self):
        self._set_location(self._user("sit1", "petpal")).assert_called_once_with("sitters.new")

    def test_parent_location_does_not(self):
        self._set_location(self._user("par1", "petparent")).assert_not_called()

    def test_becoming_a_sitter_with_a_location_announces(self):
        user = self._user("switch1", "petparent")
        self._set_location(user)
        with patch("api.core.realtime.broadcast") as bcast, self.captureOnCommitCallbacks(execute=True):
            user.user_type = "petpal"
            user.save()
        bcast.assert_called_once_with("sitters.new")


class MetricsEndpointTests(TestCase):
    """/metrics/ is only for Prometheus (token), invisible to everyone else."""

    @override_settings(METRICS_TOKEN="")
    def test_off_without_a_token(self):
        self.assertEqual(self.client.get("/metrics/").status_code, 404)

    @override_settings(METRICS_TOKEN="s3cret")
    def test_requires_the_token(self):
        self.assertEqual(self.client.get("/metrics/").status_code, 404)
        self.assertEqual(
            self.client.get("/metrics/", HTTP_AUTHORIZATION="Bearer wrong").status_code, 404
        )
        response = self.client.get("/metrics/", HTTP_AUTHORIZATION="Bearer s3cret")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"django_http_requests", response.content)


class RequestIdTests(TestCase):
    """Each request gets an id: in the response header and in its log lines."""

    def test_response_has_an_id(self):
        response = self.client.get("/delete-account/")
        self.assertRegex(response["X-Request-ID"], r"^[0-9a-f]{12}$")

    def test_client_id_is_kept_and_bad_ones_replaced(self):
        response = self.client.get("/delete-account/", HTTP_X_REQUEST_ID="app-abc123")
        self.assertEqual(response["X-Request-ID"], "app-abc123")
        response = self.client.get("/delete-account/", HTTP_X_REQUEST_ID="bad id\nINJECTED")
        self.assertRegex(response["X-Request-ID"], r"^[0-9a-f]{12}$")

    def test_log_lines_carry_the_id(self):
        import logging

        from api.core.request_id import RequestIdLogFilter, RequestIdMiddleware

        seen = {}

        def view(request):
            record = logging.LogRecord("t", logging.INFO, __file__, 1, "hi", None, None)
            RequestIdLogFilter().filter(record)
            seen["id"] = record.request_id
            from django.http import HttpResponse

            return HttpResponse("ok")

        from django.test import RequestFactory

        response = RequestIdMiddleware(view)(RequestFactory().get("/"))
        self.assertEqual(seen["id"], response["X-Request-ID"])

        # Outside a request: a placeholder, not an error.
        record = logging.LogRecord("t", logging.INFO, __file__, 1, "hi", None, None)
        RequestIdLogFilter().filter(record)
        self.assertEqual(record.request_id, "-")


class AdminDashboardTests(TestCase):
    """The admin home renders: KPI tiles (with links), charts data, lists."""

    def test_dashboard_renders(self):
        import json

        from django.conf import settings
        from django.contrib.auth import get_user_model

        User = get_user_model()
        admin = User.objects.create_superuser(email="dash@test.com", username="dash", password="pw")
        User.objects.create_user(email="o'neil@test.com", username="oneil", password="pw")
        self.client.force_login(admin)

        response = self.client.get("/" + settings.ADMIN_URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "pn-kpis")
        self.assertContains(response, "Pending Reports")
        self.assertContains(response, "?is_resolved__exact=0")  # tile links to open reports

        charts = response.context["dashboard_charts"]
        json.dumps(charts)  # chart data must be JSON-serialisable
        # One point per day for the last 30 days, quiet days included.
        self.assertEqual(len(charts["registrations"]["data"]), 30)
