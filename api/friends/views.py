"""
Views for managing friendships, friend requests, user blocks, and friend suggestions.
"""
from rest_framework import viewsets, status, generics, views, serializers
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    inline_serializer,
    OpenApiParameter,
    OpenApiTypes,
)
from rest_framework.exceptions import ValidationError, NotFound, PermissionDenied
from django.db.models import Q

from .models import FriendRequest, Friendship, UserBlock
from .serializers import (
    FriendRequestSerializer, 
    FriendshipSerializer, 
    NearbyUserSerializer,
    UserBlockSerializer,
    UserActionSerializer,
    PublicUserSerializer,
    SuggestedUserSerializer
)
from django.contrib.auth import get_user_model
from api.users.roles import MEMBER_ROLES, normalize_role
from . import services
from .paginations import FriendRequestPagination, FriendPagination, StandardResultsSetPagination
from .filters import UserFilter

class UnfriendView(views.APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=UserActionSerializer,
        responses={200: inline_serializer(
            name="UnfriendResponse",
            fields={"status": serializers.CharField()}
        )}
    )
    def post(self, request):
        serializer = UserActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        friend_id = serializer.validated_data['user_id']
            
        try:
            services.remove_friend(request.user, friend_id)
            return Response({'status': 'Friend removed successfully'})
        except NotFound as e:
            err_msg = str(e.detail[0]) if isinstance(e.detail, list) else str(e.detail)
            return Response({'error': err_msg}, status=status.HTTP_404_NOT_FOUND)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                name="type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Filter request direction.",
                required=False,
                enum=["sent", "received"],
            ),
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number for paginated results.",
                required=False,
                default=1,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of requests per page.",
                required=False,
                default=20,
            ),
        ]
    )
)
class FriendRequestViewSet(viewsets.ModelViewSet):
    serializer_class = FriendRequestSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = FriendRequestPagination

    def get_queryset(self):
        user = self.request.user
        queryset = FriendRequest.objects.select_related(
            'sender__profile', 
            'receiver__profile'
        ).filter(Q(sender=user) | Q(receiver=user)).order_by('-created_at')
        
        # Security: Filter out blocked users from showing up in pending requests
        blocked_ids = list(UserBlock.objects.filter(blocker=user).values_list('blocked_user_id', flat=True))
        blocked_by_ids = list(UserBlock.objects.filter(blocked_user=user).values_list('blocker_id', flat=True))
        exclude_ids = set(blocked_ids + blocked_by_ids)
        
        if exclude_ids:
            queryset = queryset.exclude(
                Q(sender=user, receiver_id__in=exclude_ids) | Q(receiver=user, sender_id__in=exclude_ids)
            )
        
        req_type = self.request.query_params.get('type')
        if req_type == 'sent':
            queryset = queryset.filter(sender=user)
        elif req_type == 'received':
            queryset = queryset.filter(receiver=user)
            
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = UserActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        receiver_id = serializer.validated_data['user_id']

        try:
            freq, auto_accepted = services.send_friend_request(request.user, receiver_id)
            if auto_accepted:
                return Response({'message': 'Friend request accepted automatically'}, status=status.HTTP_201_CREATED)

            res_serializer = self.get_serializer(freq)
            return Response(res_serializer.data, status=status.HTTP_201_CREATED)
        except (ValidationError, NotFound) as e:
            err_msg = str(e.detail[0]) if isinstance(e.detail, list) else str(e.detail)
            http_status = status.HTTP_404_NOT_FOUND if isinstance(e, NotFound) else status.HTTP_400_BAD_REQUEST
            return Response({'error': err_msg}, status=http_status)

    @extend_schema(
        request=UserActionSerializer,
        responses={200: inline_serializer(
            name='AcceptFriendRequestResponse',
            fields={'status': serializers.CharField()}
        )}
    )
    @action(detail=False, methods=['post'])
    def accept(self, request):
        serializer = UserActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sender_id = serializer.validated_data['user_id']

        try:
            sender_user = get_user_model().objects.get(id=sender_id)
        except get_user_model().DoesNotExist:
            return Response({'error': 'Friend request not found'}, status=status.HTTP_404_NOT_FOUND)

        if services.is_blocked(request.user, sender_user):
            return Response({'error': 'Friend request not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            friend_request = FriendRequest.objects.get(
                sender=sender_user, receiver=request.user, status='pending'
            )
        except FriendRequest.DoesNotExist:
            return Response({'error': 'Friend request not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            services.accept_friend_request(request.user, friend_request)
            return Response({'status': 'Friend request accepted'})
        except (ValidationError, PermissionDenied) as e:
            err_msg = str(e.detail[0]) if isinstance(e.detail, list) else str(e.detail)
            return Response({'error': err_msg}, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        request=UserActionSerializer,
        responses={200: inline_serializer(
            name='RejectFriendRequestResponse',
            fields={'status': serializers.CharField()}
        )}
    )
    @action(detail=False, methods=['post'])
    def reject(self, request):
        serializer = UserActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sender_id = serializer.validated_data['user_id']

        try:
            sender_user = get_user_model().objects.get(id=sender_id)
        except get_user_model().DoesNotExist:
            return Response({'error': 'Friend request not found'}, status=status.HTTP_404_NOT_FOUND)

        if services.is_blocked(request.user, sender_user):
            return Response({'error': 'Friend request not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            friend_request = FriendRequest.objects.get(
                sender=sender_user, receiver=request.user, status='pending'
            )
        except FriendRequest.DoesNotExist:
            return Response({'error': 'Friend request not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            services.reject_friend_request(request.user, friend_request)
            return Response({'status': 'Friend request rejected'})
        except (ValidationError, PermissionDenied) as e:
            err_msg = str(e.detail[0]) if isinstance(e.detail, list) else str(e.detail)
            return Response({'error': err_msg}, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        request=UserActionSerializer,
        responses={200: inline_serializer(
            name='CancelFriendRequestResponse',
            fields={'status': serializers.CharField()}
        )}
    )
    @action(detail=False, methods=['post'])
    def cancel(self, request):
        serializer = UserActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        receiver_id = serializer.validated_data['user_id']

        try:
            receiver_user = get_user_model().objects.get(id=receiver_id)
        except get_user_model().DoesNotExist:
            return Response({'error': 'Friend request not found'}, status=status.HTTP_404_NOT_FOUND)

        if services.is_blocked(request.user, receiver_user):
            return Response({'error': 'Friend request not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            friend_request = FriendRequest.objects.get(
                sender=request.user, receiver=receiver_user, status='pending'
            )
        except FriendRequest.DoesNotExist:
            return Response({'error': 'Friend request not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            services.cancel_friend_request(request.user, friend_request)
            return Response({'status': 'Friend request cancelled'})
        except (ValidationError, PermissionDenied) as e:
            err_msg = str(e.detail[0]) if isinstance(e.detail, list) else str(e.detail)
            return Response({'error': err_msg}, status=status.HTTP_400_BAD_REQUEST)



class BlockUserView(views.APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number for blocked users.",
                required=False,
                default=1,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of blocked users per page.",
                required=False,
                default=20,
            ),
        ],
        responses=UserBlockSerializer(many=True),
    )
    def get(self, request):
        blocks = UserBlock.objects.filter(blocker=request.user).order_by('-created_at')
        
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(blocks, request)
        if page is not None:
            serializer = UserBlockSerializer(page, many=True, context={'request': request})
            return paginator.get_paginated_response(serializer.data)

        serializer = UserBlockSerializer(blocks, many=True, context={'request': request})
        return Response(serializer.data)

    @extend_schema(
        request=UserActionSerializer,
        responses={200: inline_serializer(
            name="BlockUserResponse",
            fields={"status": serializers.CharField()}
        )}
    )
    def post(self, request):
        serializer = UserActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        blocked_user_id = serializer.validated_data['user_id']
            
        try:
            services.block_user(request.user, blocked_user_id)
            return Response({'status': 'User blocked successfully'})
        except NotFound as e:
            err_msg = str(e.detail[0]) if isinstance(e.detail, list) else str(e.detail)
            return Response({'error': err_msg}, status=status.HTTP_404_NOT_FOUND)

    @extend_schema(
        request=UserActionSerializer,
        responses={200: inline_serializer(
            name="UnblockUserResponse",
            fields={"status": serializers.CharField()}
        )}
    )
    def delete(self, request):
        serializer = UserActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        blocked_user_id = serializer.validated_data['user_id']
            
        try:
            services.unblock_user(request.user, blocked_user_id)
            return Response({'status': 'User unblocked successfully'})
        except NotFound as e:
             err_msg = str(e.detail[0]) if isinstance(e.detail, list) else str(e.detail)
             return Response({'error': err_msg}, status=status.HTTP_404_NOT_FOUND)


@extend_schema_view(
    get=extend_schema(
        parameters=[
            OpenApiParameter(
                name="type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Filter friends by user group.",
                required=False,
                enum=["petsitters", "petparents"],
            ),
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number for paginated results.",
                required=False,
                default=1,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of friends per page.",
                required=False,
                default=20,
            ),
        ]
    )
)
class FriendListView(generics.ListAPIView):
    serializer_class = FriendshipSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = FriendPagination

    def get_queryset(self):
        user = self.request.user
        queryset = (
            Friendship.objects.select_related("sender__profile", "receiver__profile")
            .filter(Q(sender=user) | Q(receiver=user))
            .order_by("-created_at")
        )

        # Security: Filter out blocked users from showing up in the list
        blocked_ids = list(
            UserBlock.objects.filter(blocker=user).values_list("blocked_user_id", flat=True)
        )
        blocked_by_ids = list(
            UserBlock.objects.filter(blocked_user=user).values_list("blocker_id", flat=True)
        )
        exclude_ids = set(blocked_ids + blocked_by_ids)

        if exclude_ids:
            # Exclude memberships where the other user is in exclude_ids
            queryset = queryset.exclude(
                Q(sender=user, receiver_id__in=exclude_ids)
                | Q(receiver=user, sender_id__in=exclude_ids)
            )

        # 'petsitters' or 'petparents' (legacy 'petpals' / 'petnabors' still work)
        friend_type = self.request.query_params.get("type")
        if friend_type:
            role = normalize_role(friend_type.rstrip("s"))
            if role in MEMBER_ROLES:
                # Filter on the type of the friend (the other user).
                queryset = queryset.filter(
                    Q(sender=user, receiver__user_type=role)
                    | Q(receiver=user, sender__user_type=role)
                )

        return queryset


class NewNearbyCountView(views.APIView):
    """
    GET /friends/nearby/new-count/?type=petsitter&radius=<km>&since=…

    People of that role who joined within the radius since `since` (the
    Pet Sitters tab badge). 0 without `since` or without a saved location.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(name="type", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="radius", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False,
                             description="Kilometres (default 8.05 = 5 miles)."),
            OpenApiParameter(name="since", type=OpenApiTypes.DATETIME, location=OpenApiParameter.QUERY, required=False),
        ],
        responses=inline_serializer(
            name="NewNearbyCountResponse",
            fields={"count": serializers.IntegerField(), "now": serializers.DateTimeField()},
        ),
    )
    def get(self, request):
        from api.core.new_counts import badge_response_data, capped_count, parse_since

        since = parse_since(request)
        profile = getattr(request.user, "profile", None)
        if since is None or not getattr(profile, "location_point", None):
            return Response(badge_response_data(0))

        user_filter = UserFilter(request)
        radius = user_filter.get_radius() if request.query_params.get("radius") else 8.05
        qs = services.get_nearby_users(
            request.user,
            user_type=user_filter.get_user_type() or "petsitter",
            # No "everywhere" for a "near me" badge.
            radius=radius or 8.05,
        ).filter(date_joined__gt=since).order_by()
        return Response(badge_response_data(capped_count(qs)))


class UserSearchView(views.APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="q",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search keyword for nearby users.",
                required=False,
            ),
            OpenApiParameter(
                name="type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="User type filter.",
                required=False,
                enum=["petsitter", "petparent"],
            ),
            OpenApiParameter(
                name="radius",
                type=OpenApiTypes.FLOAT,
                location=OpenApiParameter.QUERY,
                description="Distance radius in kilometers (km). Default: 1 km. Use 'all' for global search.",
                required=False,
                default=1,
            ),
            OpenApiParameter(
                name="include_friends",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                description="Include already-friended users in results (default: true).",
                required=False,
                default=True,
            ),
            OpenApiParameter(
                name="city",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="City-based filter.",
                required=False,
            ),
            OpenApiParameter(
                name="state",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="State or region filter.",
                required=False,
            ),
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number for paginated results.",
                required=False,
                default=1,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of results per page.",
                required=False,
                default=20,
            ),
        ],
        responses=NearbyUserSerializer(many=True),
    )
    def get(self, request):
        user_filter = UserFilter(request)
        
        user_type = user_filter.get_user_type()
        radius = user_filter.get_radius()
        search_query = user_filter.get_search_query()
        include_friends = user_filter.get_include_friends()
        city = user_filter.get_city()
        state = user_filter.get_state()
        
        results = services.get_nearby_users(
            request.user, 
            user_type=user_type, 
            radius=radius, 
            search_query=search_query,
            include_friends=include_friends,
            city=city,
            state=state
        )
        
        # One query for the whole page, so each result can say "Friends".
        friend_ids = set()
        for sender_id, receiver_id in Friendship.objects.filter(
            Q(sender=request.user) | Q(receiver=request.user)
        ).values_list("sender_id", "receiver_id"):
            friend_ids.add(receiver_id if sender_id == request.user.id else sender_id)
        context = {'request': request, 'friend_ids': friend_ids}

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(results, request)
        if page is not None:
            serializer = NearbyUserSerializer(page, many=True, context=context)
            return paginator.get_paginated_response(serializer.data)

        serializer = NearbyUserSerializer(results, many=True, context=context)
        return Response(serializer.data)


class PublicUserDetailsView(views.APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses=PublicUserSerializer,
    )
    def get(self, request, user_id):
        try:
            target_user = get_user_model().objects.select_related('profile').get(id=user_id, is_active=True)
        except (get_user_model().DoesNotExist, ValidationError):
            raise NotFound("User not found")

        if services.is_blocked(request.user, target_user):
            raise NotFound("User not found")

        serializer = PublicUserSerializer(target_user, context={'request': request})
        return Response(serializer.data)


class SuggestedFriendsView(views.APIView):
    """
    API view to retrieve suggested friends for the current user.
    Suggestions are calculated considering mutual friends and distance.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="limit",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Maximum suggestions to return.",
                required=False,
                default=20,
            ),
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number for paginated results.",
                required=False,
                default=1,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of suggestions per page.",
                required=False,
                default=20,
            ),
        ],
        responses=SuggestedUserSerializer(many=True),
        tags=["friends"]
    )
    def get(self, request):
        limit = request.query_params.get("limit", 20)
        try:
            limit = int(limit)
        except ValueError:
            limit = 20

        # Retrieve the un-evaluated QuerySet from the service
        suggestions_queryset = services.get_suggested_friends(request.user, limit=limit)
        
        paginator = StandardResultsSetPagination()
        # Ensure we can optionally override page size dynamically for 'limit' param if StandardResultsSetPagination supports it
        paginator.page_size = limit
        
        page = paginator.paginate_queryset(suggestions_queryset, request)
        if page is not None:
            serializer = SuggestedUserSerializer(page, many=True, context={'request': request})
            return paginator.get_paginated_response(serializer.data)

        # Fallback if pagination fails or is disabled
        serializer = SuggestedUserSerializer(suggestions_queryset[:limit], many=True, context={'request': request})
        return Response(serializer.data)
