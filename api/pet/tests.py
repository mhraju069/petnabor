import uuid

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from .models import PetProfile

User = get_user_model()


def _user():
    return User.objects.create_user(
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password="TestPass1!",
        username=f"u_{uuid.uuid4().hex[:8]}",
        is_verified=True,
        is_email_verified=True,
    )


BASE_PET = {
    "pet_name": "Bella",
    "pet_type": "Dog",
    "size": "Medium",
    "weight": "12.5",
    "weight_type": "kg",
    "date_of_birth": "2022-04-01",
    "vaccination_status": "Vaccinated",
}


class PetDetailsFieldsTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _user()
        self.client.force_authenticate(self.user)
        self.list_url = reverse("pet-list")

    def test_create_with_new_fields_json(self):
        response = self.client.post(
            self.list_url,
            {
                **BASE_PET,
                "breed": "Beagle",
                "gender": "female",
                "is_spayed_neutered": True,
                "temperament": ["playful", "good_with_kids", "playful"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["breed"], "Beagle")
        self.assertEqual(response.data["gender"], "female")
        self.assertTrue(response.data["is_spayed_neutered"])
        # duplicates removed, order kept
        self.assertEqual(response.data["temperament"], ["playful", "good_with_kids"])

    def test_new_fields_are_optional(self):
        response = self.client.post(self.list_url, BASE_PET, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["breed"], "")
        self.assertEqual(response.data["temperament"], [])
        self.assertIsNone(response.data["is_spayed_neutered"])

    def test_multipart_accepts_json_string_temperament(self):
        response = self.client.post(
            self.list_url,
            {**BASE_PET, "temperament": '["calm","shy"]', "is_spayed_neutered": "false"},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["temperament"], ["calm", "shy"])
        self.assertFalse(response.data["is_spayed_neutered"])

    def test_rejects_unknown_temperament_and_gender(self):
        bad_tag = self.client.post(
            self.list_url, {**BASE_PET, "temperament": ["grumpy"]}, format="json"
        )
        self.assertEqual(bad_tag.status_code, status.HTTP_400_BAD_REQUEST)
        bad_gender = self.client.post(
            self.list_url, {**BASE_PET, "gender": "unknown"}, format="json"
        )
        self.assertEqual(bad_gender.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_updates_temperament(self):
        pet = PetProfile.objects.create(user=self.user, **BASE_PET)
        url = reverse("pet-detail", args=[pet.id])
        response = self.client.patch(url, {"temperament": ["energetic"]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        pet.refresh_from_db()
        self.assertEqual(pet.temperament, ["energetic"])


class UserStatsTests(APITestCase):
    def test_me_includes_stats(self):
        user = _user()
        PetProfile.objects.create(user=user, **BASE_PET)
        client = APIClient()
        client.force_authenticate(user)
        response = client.get(reverse("user-detail"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["stats"], {"friends": 0, "posts": 0, "meetups": 0}
        )
