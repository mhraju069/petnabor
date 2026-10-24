from django.test import TestCase


class SeedDemoContentTest(TestCase):
    """The demo seeder builds a believable, removable dataset (no downloads in tests)."""

    def test_seed_reset_and_befriend(self):
        from io import StringIO

        from django.contrib.auth import get_user_model
        from django.core.management import call_command

        from api.friends.models import Friendship
        from api.pet.models import PetProfile
        from api.post.models import Post, PostLike
        from api.story.models import Story

        User = get_user_model()
        me = User.objects.create_user(
            email="me@test.com", username="me", phone="10000401", password="pass", is_verified=True
        )

        call_command("seed_demo_content", no_images=True, befriend=["me@test.com"], stdout=StringIO())

        demo = User.objects.filter(email__endswith="@demo.petnabor.app")
        self.assertGreaterEqual(demo.count(), 10)
        self.assertFalse(any(u.has_usable_password() for u in demo))
        self.assertTrue(all(u.profile.location_point for u in demo))
        self.assertTrue(demo.filter(user_type="petpal").exists())
        self.assertGreater(PetProfile.objects.filter(user__in=demo).count(), 10)
        self.assertGreater(Post.objects.filter(author__in=demo).count(), 20)
        self.assertTrue(PostLike.objects.exists())
        self.assertTrue(Story.objects.filter(author__in=demo).exists())
        self.assertEqual(Friendship.objects.filter(receiver=me).count(), demo.count())

        # On existing demo data, --befriend only adds friendships (several emails ok).
        other = User.objects.create_user(
            email="other@test.com", username="other", phone="10000402", password="pass", is_verified=True
        )
        call_command(
            "seed_demo_content", befriend=["other@test.com,me@test.com"], stdout=StringIO()
        )
        self.assertEqual(Friendship.objects.filter(receiver=other).count(), demo.count())
        self.assertEqual(Friendship.objects.filter(receiver=me).count(), demo.count())  # no duplicates

        # Running again without --reset refuses instead of duplicating.
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("seed_demo_content", no_images=True, stdout=StringIO())

        call_command("seed_demo_content", no_images=True, reset=True, stdout=StringIO())
        self.assertEqual(
            User.objects.filter(email__endswith="@demo.petnabor.app").count(), demo.count()
        )
        self.assertTrue(User.objects.filter(id=me.id).exists())
