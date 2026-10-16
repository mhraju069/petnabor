import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from .models import Report, ReportTargetTypeChoices

User = get_user_model()


class ReportAPITest(TestCase):
    """Tests for Report submission and management."""

    def setUp(self):
        self.client = APIClient()

        self.alice = User.objects.create_user(
            email="alice@test.com", username="alice",
            phone="10000001", password="pass",
            is_verified=True,
        )
        self.staff_user = User.objects.create_user(
            email="staff@test.com", username="staff",
            phone="10000002", password="pass",
            is_verified=True, is_staff=True,
        )
        # Profile creation handled by signal

        self.report_list_url = reverse('report-list')

    def test_submit_report(self):
        self.client.force_authenticate(self.alice)
        target_id = uuid.uuid4()
        data = {
            "target_type": ReportTargetTypeChoices.POST,
            "target_id": str(target_id),
            "reason": "Harassment",
            "description": "User is being mean."
        }
        response = self.client.post(self.report_list_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Report.objects.count(), 1)
        self.assertEqual(Report.objects.first().reporter, self.alice)

    def test_multiple_reports_allowed(self):
        self.client.force_authenticate(self.alice)
        target_id = uuid.uuid4()
        data = {
            "target_type": ReportTargetTypeChoices.POST,
            "target_id": str(target_id),
            "reason": "Spam"
        }
        # First submission
        self.client.post(self.report_list_url, data, format='json')
        # Second submission (now allowed)
        response = self.client.post(self.report_list_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Report.objects.count(), 2)

    def test_user_can_only_see_own_reports(self):
        # Alice's report
        Report.objects.create(
            reporter=self.alice, target_type=ReportTargetTypeChoices.POST,
            target_id=uuid.uuid4(), reason="R1"
        )
        # Another user's report
        bob = User.objects.create_user(email="bob@test.com", username="bob", phone="10000003", password="pass")
        # Profile creation handled by signal
        Report.objects.create(
            reporter=bob, target_type=ReportTargetTypeChoices.POST,
            target_id=uuid.uuid4(), reason="R2"
        )

        self.client.force_authenticate(self.alice)
        response = self.client.get(self.report_list_url)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['reason'], "R1")

    def test_staff_can_see_all_reports(self):
        Report.objects.create(
            reporter=self.alice, target_type=ReportTargetTypeChoices.POST,
            target_id=uuid.uuid4(), reason="R1"
        )
        self.client.force_authenticate(self.staff_user)
        response = self.client.get(self.report_list_url)
        self.assertEqual(len(response.data['results']), 1)

    def test_admin_resolve_report(self):
        report = Report.objects.create(
            reporter=self.alice, target_type=ReportTargetTypeChoices.POST,
            target_id=uuid.uuid4(), reason="R1"
        )
        self.client.force_authenticate(self.staff_user)
        url = reverse('report-resolve', kwargs={'pk': report.id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        report.refresh_from_db()
        self.assertTrue(report.is_resolved)


class ReportModerationTest(TestCase):
    """Admin moderation: remove reported content, suspend authors, resolve reports."""

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        from api.post.models import Post, PostComment
        from api.story.models import Story

        self.author = User.objects.create_user(
            email="author@test.com", username="author", phone="10000301", password="pass", is_verified=True
        )
        self.reporter = User.objects.create_user(
            email="reporter@test.com", username="reporter", phone="10000302", password="pass", is_verified=True
        )
        self.post = Post.objects.create(author=self.author, content_text="bad post")
        self.comment = PostComment.objects.create(post=self.post, user=self.author, comment_text="bad comment")
        self.story = Story.objects.create(
            author=self.author, text_content="bad story", expires_at=timezone.now() + timedelta(hours=1)
        )

    def _report(self, target_type, target_id, reporter=None):
        return Report.objects.create(
            reporter=reporter or self.reporter,
            target_type=target_type,
            target_id=target_id,
            reason="abuse",
        )

    def test_remove_post_hides_it_and_resolves_all_its_reports(self):
        from . import moderation

        other = User.objects.create_user(
            email="other@test.com", username="other", phone="10000303", password="pass", is_verified=True
        )
        r1 = self._report(ReportTargetTypeChoices.POST, self.post.id)
        r2 = self._report(ReportTargetTypeChoices.POST, self.post.id, reporter=other)

        moderation.remove_content(r1)

        self.post.refresh_from_db()
        self.assertTrue(self.post.is_deleted)
        r1.refresh_from_db()
        r2.refresh_from_db()
        self.assertTrue(r1.is_resolved and r2.is_resolved)

    def test_remove_comment_and_story(self):
        from api.post.models import PostComment
        from api.story.models import Story
        from . import moderation

        moderation.remove_content(self._report(ReportTargetTypeChoices.COMMENT, self.comment.id))
        moderation.remove_content(self._report(ReportTargetTypeChoices.STORY, self.story.id))

        self.assertFalse(PostComment.objects.filter(id=self.comment.id).exists())
        self.assertFalse(Story.objects.filter(id=self.story.id).exists())

    def test_remove_message_deletes_it_for_everyone(self):
        from api.messaging.models import Message
        from api.messaging.services import get_or_create_direct_thread
        from . import moderation

        thread, _ = get_or_create_direct_thread(self.author, self.reporter)
        message = Message.objects.create(thread=thread, sender=self.author, text_content="nasty")

        moderation.remove_content(self._report(ReportTargetTypeChoices.MESSAGE, message.id))

        message.refresh_from_db()
        self.assertTrue(message.is_deleted_for_everyone)
        self.assertIsNone(message.text_content)

    def test_suspend_author_of_post(self):
        from . import moderation

        report = self._report(ReportTargetTypeChoices.POST, self.post.id)
        moderation.suspend_owner(report)

        self.author.refresh_from_db()
        self.assertFalse(self.author.is_active)
        report.refresh_from_db()
        self.assertTrue(report.is_resolved)

    def test_removing_a_reported_user_suspends_them(self):
        from . import moderation

        moderation.remove_content(self._report(ReportTargetTypeChoices.USER, self.author.id))
        self.author.refresh_from_db()
        self.assertFalse(self.author.is_active)

    def test_staff_cannot_be_suspended(self):
        from . import moderation

        self.author.is_staff = True
        self.author.save(update_fields=["is_staff"])
        with self.assertRaises(moderation.ModerationError):
            moderation.suspend_owner(self._report(ReportTargetTypeChoices.USER, self.author.id))

    def test_already_deleted_target_just_resolves(self):
        from . import moderation

        report = self._report(ReportTargetTypeChoices.POST, uuid.uuid4())
        message = moderation.remove_content(report)
        self.assertIn("Already deleted", message)
        report.refresh_from_db()
        self.assertTrue(report.is_resolved)

    def test_admin_change_page_and_confirmation_render(self):
        admin_user = User.objects.create_superuser(
            email="admin@test.com", username="admin", phone="10000399", password="pass"
        )
        self.client.force_login(admin_user)
        report = self._report(ReportTargetTypeChoices.POST, self.post.id)

        change = self.client.get(reverse("admin:report_report_change", args=[report.id]))
        self.assertEqual(change.status_code, 200)
        self.assertContains(change, "bad post")

        confirm_url = reverse("admin:report_report_change", args=[report.id]).replace(
            "change/", "remove-content/"
        )
        confirm = self.client.get(confirm_url)
        self.assertEqual(confirm.status_code, 200)
        self.post.refresh_from_db()
        self.assertFalse(self.post.is_deleted)  # GET only asks

        self.client.post(confirm_url)
        self.post.refresh_from_db()
        self.assertTrue(self.post.is_deleted)
