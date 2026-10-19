"""
Django signals for the users app.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
import uuid
from .models import Profile


User = get_user_model()


def _generate_referral_code():
    """Generate a short unique referral code (8 uppercase alphanumeric chars)."""
    return uuid.uuid4().hex[:8].upper()


def _unique_referral_code():
    """Keep trying until we get a code that doesn't already exist."""
    while True:
        code = _generate_referral_code()
        if not Profile.objects.filter(referral_code=code).exists():
            return code


@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance, referral_code=_unique_referral_code())
    else:
        if hasattr(instance, 'profile'):
            instance.profile.save()


# ── Live "new sitter nearby" event (api/core/realtime.py) ────────────────
# A sitter shows up in "nearby" when a sitter sets/changes their location,
# or someone with a location becomes a sitter. Each tells connected apps
# (batched); the app then asks /friends/nearby/new-count/, which does the
# distance check.

from django.db import transaction  # noqa: E402
from django.db.models.signals import pre_save  # noqa: E402


def _announce_sitters() -> None:
    def send():
        try:
            from api.core.realtime import broadcast

            broadcast("sitters.new")
        except Exception:
            pass  # a missed hint only waits for the app's next foreground

    transaction.on_commit(send)


def _touches(update_fields, field: str) -> bool:
    """False when save(update_fields=[…]) can't change `field` (skip the lookup)."""
    return update_fields is None or field in update_fields


@receiver(pre_save, sender=Profile, dispatch_uid="sitter_location_before")
def _remember_old_location(sender, instance, raw=False, update_fields=None, **kwargs):
    instance._location_may_change = _touches(update_fields, "location_point")
    if raw or not instance.pk or not instance._location_may_change:
        instance._old_location = None
        return
    instance._old_location = (
        Profile.objects.filter(pk=instance.pk).values_list("location_point", flat=True).first()
    )


@receiver(post_save, sender=Profile, dispatch_uid="sitter_location_after")
def _sitter_location_changed(sender, instance, created, raw=False, **kwargs):
    if raw or not instance.location_point or not getattr(instance, "_location_may_change", True):
        return
    old = getattr(instance, "_old_location", None)
    if old is not None and old.equals(instance.location_point):
        return
    if getattr(instance.user, "user_type", None) == "petsitter":
        _announce_sitters()


@receiver(pre_save, sender=User, dispatch_uid="sitter_role_before")
def _remember_old_role(sender, instance, raw=False, update_fields=None, **kwargs):
    instance._role_may_change = _touches(update_fields, "user_type")
    instance._old_user_type = (
        None
        if raw or not instance.pk or not instance._role_may_change
        else User.objects.filter(pk=instance.pk).values_list("user_type", flat=True).first()
    )


@receiver(post_save, sender=User, dispatch_uid="sitter_role_after")
def _became_sitter(sender, instance, created, raw=False, **kwargs):
    if (
        raw
        or not getattr(instance, "_role_may_change", True)
        or instance.user_type != "petsitter"
        or getattr(instance, "_old_user_type", None) == "petsitter"
    ):
        return
    profile = Profile.objects.filter(user=instance).only("location_point").first()
    if profile and profile.location_point:
        _announce_sitters()
