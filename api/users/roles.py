"""
Member roles a user can choose for themselves: Pet Parent and Pet Sitter.

`vendor` and `admin` are also user types, but they grant access to vendor
endpoints, so users must never be able to set them on their own account.

Older app builds send the previous names (petnabor / petpal, and the even
older patnabor / patpal typos); they are accepted and mapped to the new ones.
"""

from rest_framework import serializers

from .models import UserTypes

MEMBER_ROLES = (UserTypes.PET_PARENT, UserTypes.PET_SITTER)

LEGACY_ROLE_ALIASES = {
    "petnabor": UserTypes.PET_PARENT,
    "patnabor": UserTypes.PET_PARENT,
    "petpal": UserTypes.PET_SITTER,
    "patpal": UserTypes.PET_SITTER,
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
    """A user_type input that only accepts Pet Parent / Pet Sitter."""

    def to_internal_value(self, data):
        return validate_member_role(super().to_internal_value(data))
