from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication


class ActiveAccountJWTAuthentication(JWTAuthentication):
    """
    JWT auth that also refuses deactivated accounts, so a device still
    signed in when the account was deactivated is sent back to login
    (where signing in reactivates it).
    """

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if getattr(user, "is_deactivated", False):
            raise AuthenticationFailed(
                "This account is deactivated. Log in again to turn it back on.",
                code="account_deactivated",
            )
        return user
