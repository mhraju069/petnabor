from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status
from .models import Meeting, MeetingFeedback
from api.friends.models import Friendship

User = get_user_model()

class MeetingAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user1 = User.objects.create_user(
            username='user1', email='user1@example.com', password='password123',
            first_name='User', last_name='One'
        )
        self.user2 = User.objects.create_user(
            username='user2', email='user2@example.com', password='password123',
            first_name='User', last_name='Two'
        )
        self.user3 = User.objects.create_user(
            username='user3', email='user3@example.com', password='password123',
            first_name='User', last_name='Three'
        )

        # Make user1 and user2 friends
        Friendship.objects.create(sender=self.user1, receiver=self.user2)

    def test_create_meeting_with_friend(self):
        self.client.force_authenticate(user=self.user1)
        data = {
            'receiver_id': str(self.user2.id),
            'visitor_name': 'Test Visitor',
            'visitor_phone': '1234567890',
            'visit_date': '2030-01-01',
            'visit_time': '10:00:00',
            'reason': 'Meet & Greet',
            'address_street': '123 Test St',
            'city': 'Test City',
            'state': 'Test State',
            'zipcode': '12345',
            'message': 'Hello friend'
        }
        response = self.client.post('/api/meetings/requests/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Meeting.objects.count(), 1)
        meeting = Meeting.objects.first()
        self.assertEqual(meeting.status, 'PENDING')

    def test_create_meeting_with_non_friend(self):
        self.client.force_authenticate(user=self.user1)
        data = {
            'receiver_id': str(self.user3.id),
            'visitor_name': 'Test Visitor',
            'visitor_phone': '1234567890',
            'visit_date': '2030-01-01',
            'visit_time': '10:00:00',
            'reason': 'Meet & Greet',
            'address_street': '123 Test St',
            'city': 'Test City',
            'state': 'Test State',
            'zipcode': '12345',
            'message': 'Hello friend'
        }
        response = self.client.post('/api/meetings/requests/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(Meeting.objects.count(), 0)

    def test_accept_meeting(self):
        meeting = Meeting.objects.create(
            sender=self.user1, receiver=self.user2,
            visitor_name='Test Visitor', visitor_phone='1234567890',
            visit_date='2030-01-01', visit_time='10:00:00',
            reason='Meet & Greet', address_street='123 Test St',
            city='Test City', state='Test State', zipcode='12345'
        )
        
        self.client.force_authenticate(user=self.user2)
        response = self.client.post(f'/api/meetings/requests/{meeting.id}/accept/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, 'ACCEPTED')

    def test_complete_meeting(self):
        # Started yesterday: can be completed.
        meeting = Meeting.objects.create(
            sender=self.user1, receiver=self.user2,
            visitor_name='Test Visitor', visitor_phone='1234567890',
            visit_date=(timezone.localdate() - timedelta(days=1)).isoformat(), visit_time='10:00:00',
            reason='Meet & Greet', address_street='123 Test St',
            city='Test City', state='Test State', zipcode='12345',
            status='ACCEPTED'
        )
        
        self.client.force_authenticate(user=self.user1)
        response = self.client.post(f'/api/meetings/requests/{meeting.id}/complete/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, 'COMPLETED')

    def test_give_feedback(self):
        meeting = Meeting.objects.create(
            sender=self.user1, receiver=self.user2,
            visitor_name='Test Visitor', visitor_phone='1234567890',
            visit_date='2020-01-01', visit_time='10:00:00',
            reason='Meet & Greet', address_street='123 Test St',
            city='Test City', state='Test State', zipcode='12345',
            status='COMPLETED'
        )

        self.client.force_authenticate(user=self.user1)
        data = {
            'meeting': str(meeting.id),
            'reviewee': str(self.user2.id),
            'rating': 5,
            'feedback_text': 'Great meeting!'
        }
        response = self.client.post('/api/meetings/feedback/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(MeetingFeedback.objects.count(), 1)


class MeetingTimingRulesTest(TestCase):
    """Future meet-ups can't be completed; past ones can't be booked/accepted."""

    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(
            username='alice_t', email='alice_t@example.com', password='password123'
        )
        self.bob = User.objects.create_user(
            username='bob_t', email='bob_t@example.com', password='password123'
        )
        Friendship.objects.create(sender=self.alice, receiver=self.bob)

    def _meeting(self, days_from_today, status_value='ACCEPTED'):
        return Meeting.objects.create(
            sender=self.alice, receiver=self.bob,
            visitor_name='V', visitor_phone='1234567890',
            visit_date=(timezone.localdate() + timedelta(days=days_from_today)).isoformat(),
            visit_time='10:00:00', reason='Meet & Greet',
            address_street='1 St', city='C', state='S', zipcode='12345',
            status=status_value,
        )

    def test_cannot_complete_future_meeting(self):
        meeting = self._meeting(days_from_today=3)
        self.client.force_authenticate(user=self.alice)
        response = self.client.post(f'/api/meetings/requests/{meeting.id}/complete/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, 'ACCEPTED')

    def test_non_participant_cannot_complete(self):
        outsider = User.objects.create_user(
            username='eve_t', email='eve_t@example.com', password='password123'
        )
        meeting = self._meeting(days_from_today=-1)
        self.client.force_authenticate(user=outsider)
        response = self.client.post(f'/api/meetings/requests/{meeting.id}/complete/')
        self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))

    def test_cannot_accept_meeting_that_already_passed(self):
        meeting = self._meeting(days_from_today=-3, status_value='PENDING')
        self.client.force_authenticate(user=self.bob)
        response = self.client.post(f'/api/meetings/requests/{meeting.id}/accept/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_create_meeting_in_the_past(self):
        self.client.force_authenticate(user=self.alice)
        response = self.client.post('/api/meetings/requests/', {
            'receiver_id': self.bob.id,
            'visitor_name': 'V', 'visitor_phone': '1234567890',
            'visit_date': (timezone.localdate() - timedelta(days=3)).isoformat(),
            'visit_time': '10:00:00', 'reason': 'Meet & Greet',
            'address_street': '1 St', 'city': 'C', 'state': 'S', 'zipcode': '12345',
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # The API's error format nests field errors under "errors".
        self.assertIn('visit_date', response.data['errors'])


class MeetingParticipantPrivacyTest(TestCase):
    """Meeting participants must not see each other's private profile data."""

    def test_participant_profiles_hide_address_and_birthday(self):
        from datetime import date, time, timedelta

        from django.contrib.auth import get_user_model
        from django.contrib.gis.geos import Point

        from api.users.models import Profile
        from .models import Meeting
        from .serializers import MeetingSerializer

        User = get_user_model()
        alice = User.objects.create_user(
            email="pa@test.com", username="pa", phone="10000201", password="pass", is_verified=True
        )
        bob = User.objects.create_user(
            email="pb@test.com", username="pb", phone="10000202", password="pass", is_verified=True
        )
        profile, _ = Profile.objects.get_or_create(user=alice)
        profile.address_street = "12 Secret St"
        profile.zipcode = "10001"
        profile.city = "Springfield"
        profile.date_of_birth = date(1990, 1, 1)
        profile.location_point = Point(-73.9, 40.7, srid=4326)
        profile.save()

        meeting = Meeting.objects.create(
            sender=alice,
            receiver=bob,
            visit_date=date.today() + timedelta(days=2),
            visit_time=time(10, 0),
        )
        # Fresh from the DB: `alice` still caches the profile the signal created.
        meeting = Meeting.objects.select_related("sender__profile").get(pk=meeting.pk)
        data = MeetingSerializer(meeting).data
        sender_profile = data["sender"]["profile"]

        self.assertEqual(sender_profile["city"], "Springfield")
        for private in ("address_street", "apartment", "zipcode", "location_point", "date_of_birth", "referral_code"):
            self.assertNotIn(private, sender_profile)


class RemovePreviousMeetingTest(TestCase):
    """Removing a previous meeting hides it for you only; upcoming can't be removed."""

    # Same two friends and meeting factory as the timing tests.
    setUp = MeetingTimingRulesTest.setUp
    _meeting = MeetingTimingRulesTest._meeting

    def _previous_ids(self, user):
        self.client.force_authenticate(user=user)
        response = self.client.get('/api/meetings/requests/previous/')
        results = response.data.get('results', response.data)
        return {str(m['id']) for m in results}

    def test_remove_hides_it_for_me_only_and_keeps_reviews(self):
        meeting = self._meeting(days_from_today=-3, status_value='COMPLETED')
        MeetingFeedback.objects.create(
            meeting=meeting, reviewer=self.bob, reviewee=self.alice, rating=5, feedback_text='Great!'
        )

        self.client.force_authenticate(user=self.alice)
        response = self.client.delete(f'/api/meetings/requests/{meeting.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        self.assertNotIn(str(meeting.id), self._previous_ids(self.alice))
        self.assertIn(str(meeting.id), self._previous_ids(self.bob))  # Bob keeps it
        self.assertTrue(Meeting.objects.filter(pk=meeting.pk).exists())
        self.assertEqual(MeetingFeedback.objects.filter(meeting=meeting).count(), 1)  # review stays

    def test_upcoming_meeting_cannot_be_removed(self):
        meeting = self._meeting(days_from_today=2, status_value='ACCEPTED')
        self.client.force_authenticate(user=self.alice)
        response = self.client.delete(f'/api/meetings/requests/{meeting.id}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(meeting.hidden_for.exists())

    def test_cancelled_upcoming_meeting_can_be_removed(self):
        meeting = self._meeting(days_from_today=2, status_value='CANCELLED')
        self.client.force_authenticate(user=self.alice)
        response = self.client.delete(f'/api/meetings/requests/{meeting.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_meetings_cannot_be_edited_directly(self):
        meeting = self._meeting(days_from_today=2, status_value='PENDING')
        self.client.force_authenticate(user=self.bob)
        response = self.client.patch(
            f'/api/meetings/requests/{meeting.id}/', {'status': 'COMPLETED'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, 'PENDING')
