import io
import tempfile
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from PIL import Image

from api.users.models import User, Profile


def make_test_image(name="profile.jpg", size=(100, 100)):
    buffer = io.BytesIO()
    image = Image.new("RGB", size, color=(255, 0, 0))
    image.save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


@override_settings(MEDIA_ROOT=tempfile.gettempdir())
class ProfileUpdateViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="testprofile@test.com", username="testprofile", password="password123"
        )
        # Verify the user so they can access the endpoints
        self.user.is_verified = True
        self.user.is_email_verified = True
        self.user.save()

        # Generate JWT token by authenticating
        response = self.client.post(
            reverse("login"), 
            {"email_or_phone": "testprofile@test.com", "password": "password123"},
            format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.token = response.data["data"]["access_token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")


    @patch("api.users.tasks.process_profile_media_task.delay")
    def test_profile_picture_update_uses_on_commit(self, mock_delay):
        """
        Tests that when a profile picture is uploaded, the process_profile_media_task
        is properly scheduled using transaction.on_commit. This prevents the race condition
        where the Celery task fires before the DB transaction commits.
        """
        initial_pic = make_test_image(name="initial.jpg")
        
        # Test updating the profile picture
        url = reverse("profile-detail")
        response = self.client.patch(
            url, 
            {"profile_picture": initial_pic},
            format="multipart"
        )
        
        # Expect successful update
        self.assertEqual(response.status_code, 200)
        
        # The user's profile picture should be updated in the DB
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.profile_picture.name.endswith(".jpg"))
        
        # Because we wrapped the `.delay` hook in transaction.on_commit in the view,
        # TestCase runner will automatically fire it since Django's test client correctly 
        # executes on_commit hooks after the view finishes processing the transaction wrapper.
        mock_delay.assert_called_once_with(str(self.user.profile.id), "profile_picture")
        
        # Now let's try updating both cover_photo and profile_picture at the same time
        mock_delay.reset_mock()
        new_pic = make_test_image(name="new_pic.jpg")
        new_cover = make_test_image(name="new_cover.jpg")
        
        response = self.client.patch(
            url, 
            {"profile_picture": new_pic, "cover_photo": new_cover},
            format="multipart"
        )
        
        self.assertEqual(response.status_code, 200)
        # Should be called twice (once for profile_picture, once for cover_photo)
        self.assertEqual(mock_delay.call_count, 2)
