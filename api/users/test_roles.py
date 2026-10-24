"""
Tests for member roles (Pet Parent / PetPal): defaults, legacy aliases,
and that users can't give themselves the vendor/admin user types.
"""

import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from .models import UserTypes
from .roles import normalize_role, validate_member_role

User = get_user_model()
PASSWORD = "TestPass1!"


def _verified_user(user_type=UserTypes.PET_PARENT, **extra):
    return User.objects.create_user(
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password=PASSWORD,
        username=f"u_{uuid.uuid4().hex[:8]}",
        is_verified=True,
        is_email_verified=True,
        user_type=user_type,
        **extra,
    )


class RoleHelperTests(TestCase):
    def test_legacy_names_map_to_new_roles(self):
        for legacy, expected in [
            ("petnabor", "petparent"),
            ("patnabor", "petparent"),
            ("petsitter", "petpal"),
            ("PetSitter", "petpal"),
            ("PatPal", "petpal"),
            ("petpal", "petpal"),
            ("petparent", "petparent"),
        ]:
            self.assertEqual(normalize_role(legacy), expected)

    def test_staff_types_are_not_member_roles(self):
        from rest_framework.exceptions import ValidationError

        for value in ["vendor", "admin", "", "superuser"]:
            with self.assertRaises(ValidationError):
                validate_member_role(value)


@patch("api.users.services.send_email_otp")
class SignupRoleTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("signup")

    def _signup(self, **extra):
        return self.client.post(self.url, {
            "email": f"{uuid.uuid4().hex[:8]}@example.com",
            "password": PASSWORD,
            **extra,
        })

    def _created_type(self, response):
        return User.objects.get(id=response.data["data"]["user"]["id"]).user_type

    def test_defaults_to_pet_parent(self, _mock):
        response = self._signup()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self._created_type(response), UserTypes.PET_PARENT)

    def test_accepts_pet_pal(self, _mock):
        response = self._signup(user_type="petpal")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self._created_type(response), UserTypes.PET_PAL)

    def test_maps_legacy_value(self, _mock):
        # Older app builds still send "petsitter".
        response = self._signup(user_type="petsitter")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self._created_type(response), UserTypes.PET_PAL)

    def test_rejects_vendor_and_admin(self, _mock):
        for value in ["vendor", "admin"]:
            response = self._signup(user_type=value)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class UserTypeUpdateTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("user-detail")

    def test_member_can_switch_role(self):
        user = _verified_user()
        self.client.force_authenticate(user)
        response = self.client.patch(self.url, {"user_type": "petpal"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertEqual(user.user_type, UserTypes.PET_PAL)

    def test_legacy_value_is_normalised(self):
        user = _verified_user()
        self.client.force_authenticate(user)
        response = self.client.patch(self.url, {"user_type": "petsitter"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertEqual(user.user_type, UserTypes.PET_PAL)

    def test_member_cannot_become_vendor_or_admin(self):
        user = _verified_user()
        self.client.force_authenticate(user)
        for value in ["vendor", "admin"]:
            response = self.client.patch(self.url, {"user_type": value})
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        user.refresh_from_db()
        self.assertEqual(user.user_type, UserTypes.PET_PARENT)

    def test_vendor_cannot_switch_to_member_role(self):
        user = _verified_user(user_type=UserTypes.VENDOR)
        self.client.force_authenticate(user)
        response = self.client.patch(self.url, {"user_type": "petparent"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_staff_can_set_any_type(self):
        user = _verified_user(is_staff=True)
        self.client.force_authenticate(user)
        response = self.client.patch(self.url, {"user_type": "vendor"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
