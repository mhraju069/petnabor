from api.users.roles import MEMBER_ROLES, normalize_role


class UserFilter:
    """
    Professional filter class to handle advanced user search and discovery.
    """
    def __init__(self, request):
        self.request = request
        self.params = request.query_params

    def get_user_type(self):
        """Returns the user type to filter by, or None for all."""
        u_type = normalize_role(self.params.get('type'))
        if u_type in MEMBER_ROLES:
            return u_type
        return None

    def get_radius(self):
        """Returns the search radius in kilometers, or None for global search.
        
        Default: 1 km. Pass 'all' (or '0'/'none') for no distance restriction.
        """
        val = self.params.get('radius')
        if val in ['all', '0', 'none', None, '']:
            return None
        try:
            radius = float(val)
            if radius <= 0:
                return None
            return radius
        except (ValueError, TypeError):
            return 1.0  # Default: 1 km

    def get_city(self):
        """Returns the city to filter by."""
        return self.params.get('city', '').strip()

    def get_state(self):
        """Returns the state to filter by."""
        return self.params.get('state', '').strip()

    def get_search_query(self):
        """Returns the text search query."""
        return self.params.get('search', '').strip()

    def get_include_friends(self):
        """Returns whether to include existing friends in search results.
        Defaults to True (show friends) — callers can pass include_friends=false to exclude.
        """
        val = self.params.get('include_friends', 'true').lower()
        return val == 'true'
