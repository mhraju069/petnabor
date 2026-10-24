"""
Member roles a user can choose for themselves: Pet Parent and PetPal.

`vendor` and `admin` are also user types, but they grant access to vendor
endpoints, so users must never be able to set them on their own account.

Older app builds send previous names (petsitter; petnabor; and the even
older patnabor / patpal typos); they are accepted and mapped to the current
ones.
"""

from rest_framework import serializers

from .models import UserTypes

MEMBER_ROLES = (UserTypes.PET_PARENT, UserTypes.PET_PAL)

LEGACY_ROLE_ALIASES = {
    "petsitter": UserTypes.PET_PAL,
    "patpal": UserTypes.PET_PAL,
    "petnabor": UserTypes.PET_PARENT,
    "patnabor": UserTypes.PET_PARENT,
}


def normalize_role(value):
    """Map legacy names to the current value; returns other input unchanged."""
    if value is None:
        return value
    cleaned = str(value).strip().lower()
    return LEGACY_ROLE_ALIASES.get(cleaned, cleaned)


def validate_member_role(value):
    """Return a valid member role, or raise a ValidationError."""
    role = normalize_role(value)
    if role not in MEMBER_ROLES:
        raise serializers.ValidationError(
            f"user_type must be one of: {', '.join(MEMBER_ROLES)}."
        )
    return role


class MemberRoleField(serializers.CharField):
    """A user_type input that only accepts Pet Parent / PetPal."""

    def to_internal_value(self, data):
        return validate_member_role(super().to_internal_value(data))
