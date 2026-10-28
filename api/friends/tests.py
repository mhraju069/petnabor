from django.test import TestCase
from django.contrib.auth import get_user_model
from unittest.mock import patch
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.gis.geos import Point

from .models import FriendRequest, Friendship, UserBlock
from api.users.models import Profile, UserTypes

User = get_user_model()

class FriendsAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        
        # Create users
        self.user1 = User.objects.create_user(
            email="user1@example.com", 
            username="user1",
            phone="111111111", 
            password="testpassword",
            first_name="User", 
            last_name="One",
            user_type=UserTypes.PET_PAL
        )
        self.user2 = User.objects.create_user(
            email="user2@example.com", 
            username="user2",
            phone="222222222", 
            password="testpassword",
            first_name="User", 
            last_name="Two",
            user_type=UserTypes.PET_PAL
        )
        self.user3 = User.objects.create_user(
            email="user3@example.com", 
            username="user3",
            phone="333333333", 
            password="testpassword",
            first_name="User", 
            last_name="Three",
            user_type=UserTypes.PET_PARENT
        )
        self.user4 = User.objects.create_user(
            email="user4@example.com", 
            username="user4",
            phone="444444444", 
            password="testpassword",
            first_name="User", 
            last_name="Four",
            user_type=UserTypes.PET_PAL
        )
        
        for user in [self.user1, self.user2, self.user3, self.user4]:
            Profile.objects.update_or_create(
                user=user, 
                defaults={'location_point': Point(0.0, 0.0)}
            )
        
        # Assign cities for testing address filters
        self.user1.profile.city = "Dhaka"
        self.user1.profile.save()
        self.user2.profile.city = "Dhaka"
        self.user2.profile.save()
        self.user3.profile.city = "Chittagong"
        self.user3.profile.save()
        self.user4.profile.city = "Sylhet"
        self.user4.profile.save()

    def test_send_friend_request(self):
        self.client.force_authenticate(user=self.user1)
        url = reverse('friend-requests-list')
        data = {'user_id': str(self.user2.id)}
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(FriendRequest.objects.count(), 1)
        
    def test_send_friend_request_to_self(self):
        self.client.force_authenticate(user=self.user1)
        url = reverse('friend-requests-list')
        data = {'user_id': str(self.user1.id)}
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_accept_friend_request(self):
        req = FriendRequest.objects.create(sender=self.user1, receiver=self.user2, status='pending')
        req_id = req.id

        self.client.force_authenticate(user=self.user2)
        url = reverse('friend-requests-accept')
        response = self.client.post(url, {'user_id': str(self.user1.id)}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # FriendRequest should be deleted after accept
        self.assertFalse(FriendRequest.objects.filter(id=req_id).exists())
        # Friendship should be created
        self.assertTrue(
            Friendship.objects.filter(sender=self.user1, receiver=self.user2).exists()
            or Friendship.objects.filter(sender=self.user2, receiver=self.user1).exists()
        )

    def test_reject_friend_request(self):
        req = FriendRequest.objects.create(sender=self.user1, receiver=self.user2, status='pending')
        
        self.client.force_authenticate(user=self.user2)
        url = reverse('friend-requests-reject')
        response = self.client.post(url, {'user_id': str(self.user1.id)}, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(FriendRequest.objects.filter(id=req.id).exists())
        
    def test_cancel_friend_request(self):
        req = FriendRequest.objects.create(sender=self.user1, receiver=self.user2, status='pending')
        
        self.client.force_authenticate(user=self.user1)
        url = reverse('friend-requests-cancel')
        response = self.client.post(url, {'user_id': str(self.user2.id)}, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(FriendRequest.objects.filter(id=req.id).exists())

    def test_block_user(self):
        self.client.force_authenticate(user=self.user1)
        url = reverse('block-user')
        data = {'user_id': str(self.user2.id)}
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(UserBlock.objects.filter(blocker=self.user1, blocked_user=self.user2).exists())
        
        self.client.force_authenticate(user=self.user2)
        req_url = reverse('friend-requests-list')
        req_data = {'receiver_id': str(self.user1.id)}
        req_response = self.client.post(req_url, req_data, format='json')
        self.assertEqual(req_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_block_back(self):
        # user2 already blocked user1: user1 can't block user2 back.
        UserBlock.objects.create(blocker=self.user2, blocked_user=self.user1)
        self.client.force_authenticate(user=self.user1)
        response = self.client.post(
            reverse('block-user'), {'user_id': str(self.user2.id)}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(UserBlock.objects.filter(blocker=self.user1, blocked_user=self.user2).exists())

    def test_unblock_user(self):
        UserBlock.objects.create(blocker=self.user1, blocked_user=self.user2)
        self.client.force_authenticate(user=self.user1)
        
        url = reverse('block-user')
        data = {'user_id': str(self.user2.id)}
        response = self.client.delete(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(UserBlock.objects.filter(blocker=self.user1, blocked_user=self.user2).exists())

    def test_get_blocks(self):
        UserBlock.objects.create(blocker=self.user1, blocked_user=self.user2)
        self.client.force_authenticate(user=self.user1)
        url = reverse('block-user')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)

    def test_remove_friend(self):
        Friendship.objects.create(sender=self.user1, receiver=self.user2)
        self.client.force_authenticate(user=self.user1)
        
        url = reverse('remove-friend')
        data = {'user_id': str(self.user2.id)}
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Friendship.objects.filter(sender=self.user1, receiver=self.user2).exists())

    def test_friend_list(self):
        Friendship.objects.create(sender=self.user1, receiver=self.user2)
        Friendship.objects.create(sender=self.user1, receiver=self.user3)
        
        self.client.force_authenticate(user=self.user1)
        url = reverse('list-friends')
        
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 2)
        
        response_pals = self.client.get(url + '?type=petpals')
        self.assertEqual(len(response_pals.data['results']), 1)
        
        response_nabors = self.client.get(url + '?type=petnabors')
        self.assertEqual(len(response_nabors.data['results']), 1)

    def test_nearby_users_search(self):
        self.client.force_authenticate(user=self.user1)
        url = reverse('search-users')
        
        # radius=1 means 1km. All users are at Point(0,0) so all are in range.
        # include_friends=true by default. The searcher (user1) is never listed.
        # user2(petpal), user4(petpal) → 2 results. Sent as the legacy
        # "petsitter" (older app builds), which still maps to petpal.
        response = self.client.get(url + '?type=petsitter&radius=1')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        usernames = {u['username'] for u in response.data['results']}
        self.assertEqual(usernames, {'user2', 'user4'})
        # New fields for the "Find PetPals" cards.
        first = response.data['results'][0]
        self.assertIn('city', first)
        self.assertIn('is_friend', first)
        cities = {u['username']: u['city'] for u in response.data['results']}
        self.assertEqual(cities, {'user2': 'Dhaka', 'user4': 'Sylhet'})

    def test_advanced_search_filters(self):
        # Establish friendships first: user1 is friends with user2 and user3
        Friendship.objects.create(sender=self.user1, receiver=self.user2)
        Friendship.objects.create(sender=self.user1, receiver=self.user3)
        
        self.client.force_authenticate(user=self.user1)
        url = reverse('search-users')
        
        # 1. Test "All" users — default include_friends=true, self is excluded.
        # Should see user2, user3, user4; friends flagged with is_friend.
        response_all = self.client.get(url + '?radius=1')
        self.assertEqual(response_all.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_all.data['results']), 3)
        flags = {u['username']: u['is_friend'] for u in response_all.data['results']}
        self.assertEqual(flags, {'user2': True, 'user3': True, 'user4': False})

        # 2. Test explicit friend exclusion (include_friends=false)
        # Friends excluded: user2, user3. Self never shows. Remaining: user4.
        response_no_friends = self.client.get(url + '?radius=1&include_friends=false')
        usernames = [u['username'] for u in response_no_friends.data['results']]
        self.assertEqual(usernames, ['user4'])

        # 3. Test specific type with friends included (default)
        response_nabors = self.client.get(url + '?type=petnabor&radius=1')
        # user3 is petnabor and a friend — included by default.
        self.assertEqual(len(response_nabors.data['results']), 1)
        self.assertEqual(response_nabors.data['results'][0]['username'], 'user3')

    def test_global_search(self):
        self.client.force_authenticate(user=self.user1)
        url = reverse('search-users')
        
        # Global search (radius=all) — no distance restriction.
        # include_friends=true by default. The searcher is never listed.
        # Should see user2, user3, user4.
        response_all = self.client.get(url + '?radius=all')
        self.assertEqual(len(response_all.data['results']), 3)

    def test_city_search(self):
        self.client.force_authenticate(user=self.user1)
        url = reverse('search-users')
        
        # City search — user1 (the searcher) is in Dhaka but never listed.
        response_dhaka = self.client.get(url + '?radius=all&city=Dhaka')
        usernames = [u['username'] for u in response_dhaka.data['results']]
        self.assertEqual(usernames, ['user2'])
        
        response_sylhet = self.client.get(url + '?radius=all&city=Sylhet')
        self.assertEqual(len(response_sylhet.data['results']), 1)
        self.assertEqual(response_sylhet.data['results'][0]['username'], 'user4')

    @patch('api.friends.services.send_notification')
    def test_friend_request_notifications(self, mock_notify):
        from api.notifications.models import NotificationTypes
        self.client.force_authenticate(user=self.user1)
        url = reverse('friend-requests-list')
        data = {'user_id': str(self.user2.id)}
        self.client.post(url, data, format='json')

        # Check if notification was sent to user2 with deeplink data
        self.assertEqual(mock_notify.call_count, 1)
        call_kwargs = mock_notify.call_args.kwargs
        self.assertEqual(call_kwargs['user_id'], self.user2.id)
        self.assertEqual(call_kwargs['notification_type'], NotificationTypes.FRIEND_REQUEST)
        self.assertIn('sender_id', call_kwargs.get('data', {}))
        self.assertIn('request_id', call_kwargs.get('data', {}))
        self.assertEqual(call_kwargs['data']['sender_id'], str(self.user1.id))

    @patch('api.friends.services.send_notification')
    def test_accept_friend_request_notification(self, mock_notify):
        from api.notifications.models import NotificationTypes
        req = FriendRequest.objects.create(sender=self.user1, receiver=self.user2, status='pending')

        self.client.force_authenticate(user=self.user2)
        url = reverse('friend-requests-accept')
        self.client.post(url, {'user_id': str(self.user1.id)}, format='json')

        # sender (user1) gets notified with deeplink data (accepter_id only, no request_id)
        self.assertEqual(mock_notify.call_count, 1)
        call_kwargs = mock_notify.call_args.kwargs
        self.assertEqual(call_kwargs['user_id'], self.user1.id)
        self.assertEqual(call_kwargs['notification_type'], NotificationTypes.FRIEND_ACCEPT)
        self.assertIn('accepter_id', call_kwargs.get('data', {}))
        self.assertNotIn('request_id', call_kwargs.get('data', {}))
        self.assertEqual(call_kwargs['data']['accepter_id'], str(self.user2.id))

    def test_public_user_detail(self):
        self.client.force_authenticate(user=self.user1)
        url = reverse('user-detail', kwargs={'user_id': self.user3.id})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['username'], 'user3')
        # Sensitive data should be missing
        self.assertNotIn('email', response.data)
        self.assertNotIn('phone', response.data)
        # Friendship status should be correctly reported
        self.assertEqual(response.data['friendship_status'], 'none')

    def test_public_user_detail_blocked(self):
        # user2 blocks user1
        UserBlock.objects.create(blocker=self.user2, blocked_user=self.user1)
        
        self.client.force_authenticate(user=self.user1)
        url = reverse('user-detail', kwargs={'user_id': self.user2.id})
        response = self.client.get(url)
        
        # Should be 404 for security/privacy (obfuscating block)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_suggested_friends_filtering(self):
        # Establish friendships
        Friendship.objects.create(sender=self.user1, receiver=self.user2)
        # Block user4
        UserBlock.objects.create(blocker=self.user1, blocked_user=self.user4)
        
        self.client.force_authenticate(user=self.user1)
        url = reverse('suggested-friends')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)
        # suggestions should exclude user1 (self), user2 (friend), user4 (blocked).
        # only user3 should be suggested.
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['username'], 'user3')
        self.assertEqual(results[0]['mutual_friends_count'], 0)
        self.assertEqual(results[0]['friendship_status'], 'none')

    def test_suggested_friends_scoring(self):
        # Create a 5th user
        user5 = User.objects.create_user(
            email="user5@example.com", username="user5", phone="555555555", password="test",
            first_name="User", last_name="Five", user_type=UserTypes.PET_PAL
        )
        Profile.objects.update_or_create(user=user5, defaults={'location_point': Point(0.0, 0.0)})
        
        # user1 is the current user. Target is user3 and user5.
        # user3 has 2 mutual friends with user1 (user2 and user4)
        # user5 has 0 mutual friends.
        Friendship.objects.create(sender=self.user1, receiver=self.user2)
        Friendship.objects.create(sender=self.user1, receiver=self.user4)
        
        # user3's friends (mutual with user1)
        Friendship.objects.create(sender=self.user3, receiver=self.user2)
        Friendship.objects.create(sender=self.user3, receiver=self.user4)
        
        self.client.force_authenticate(user=self.user1)
        url = reverse('suggested-friends')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)
        # suggestions should exclude user1 (self), user2, user4 (friends).
        # user3 and user5 are suggested.
        self.assertEqual(len(results), 2)
        
        # user3 should be ranked first because of 2 mutual friends (score = 2 * 10 = 20 + distance bonus).
        self.assertEqual(results[0]['username'], 'user3')
        self.assertEqual(results[0]['mutual_friends_count'], 2)
        
        self.assertEqual(results[1]['username'], 'user5')
        self.assertEqual(results[1]['mutual_friends_count'], 0)



class NewNearbyCountTest(TestCase):
    """PetPals tab badge: sitters who joined near me since my last visit."""

    def _user(self, name, user_type, point):
        from api.users.models import Profile

        user = User.objects.create_user(
            email=f"{name}@test.com", username=name, password="pass",
            is_verified=True, user_type=user_type,
        )
        Profile.objects.update_or_create(user=user, defaults={"location_point": point})
        # Fresh copy: `user.profile` is still the profile cached at signup
        # (no location), which made the view think "no saved location".
        return User.objects.select_related("profile").get(pk=user.pk)

    def test_counts_new_nearby_sitters_only(self):
        from datetime import timedelta

        from django.utils import timezone

        me = self._user("parent", "petparent", Point(0.0, 0.0))
        self.client = APIClient()
        self.client.force_authenticate(me)
        url = reverse("nearby-new-count")

        first = self.client.get(url, {"type": "petpal"})
        self.assertEqual(first.data["count"], 0)
        since = first.data["now"]

        old = self._user("oldsitter", "petpal", Point(0.0, 0.0))
        User.objects.filter(pk=old.pk).update(date_joined=timezone.now() - timedelta(days=3))
        self._user("newsitter", "petpal", Point(0.0, 0.0))
        self._user("newparent", "petparent", Point(0.0, 0.0))
        self._user("farsitter", "petpal", Point(10.0, 10.0))  # ~1,500 km away

        response = self.client.get(url, {"type": "petpal", "since": since})
        self.assertEqual(response.data["count"], 1)

    def test_zero_without_a_location(self):
        from api.users.models import Profile

        me = User.objects.create_user(
            email="noloc@test.com", username="noloc", password="pass", is_verified=True,
        )
        Profile.objects.get_or_create(user=me)
        self.client = APIClient()
        self.client.force_authenticate(me)
        response = self.client.get(
            reverse("nearby-new-count"), {"type": "petpal", "since": "2020-01-01T00:00:00Z"}
        )
        self.assertEqual(response.data["count"], 0)
