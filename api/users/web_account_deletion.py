"""
Public "Delete your account" web page (Google Play requires one that works
without the app). https://<backend>/delete-account/

1. The person enters their account email.
2. We email a link signed for that account (valid 24 hours). The page says
   the same thing whether or not the email has an account, so it can't be
   used to find out who uses PetNabor.
3. The link opens a confirmation page; pressing the button (a POST, so mail
   scanners that open links can't trigger it) deletes the account with the
   same delete_account() as the app.
"""

import logging

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.shortcuts import render
from django.urls import reverse
from django.utils.html import escape
from django.views.decorators.http import require_http_methods

from api.notifications.email_service import EmailService

from .account_deletion import AccountDeletionBlocked, delete_account

logger = logging.getLogger(__name__)
User = get_user_model()

TOKEN_SALT = "web-account-deletion"
TOKEN_MAX_AGE = 60 * 60 * 24  # 24 hours
REQUESTS_PER_HOUR = 5  # per IP, and per email address

TEMPLATE = "web/delete_account.html"


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR", "")


def _over_limit(key: str) -> bool:
    cache_key = f"web_delete_account:{key}"
    cache.add(cache_key, 0, 60 * 60)
    try:
        count = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, 60 * 60)
        count = 1
    return count > REQUESTS_PER_HOUR


def _send_link(request, user) -> None:
    token = signing.dumps({"u": str(user.pk)}, salt=TOKEN_SALT)
    link = request.build_absolute_uri(reverse("web-delete-account-confirm")) + f"?token={token}"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;color:#1F2933">
      <h2 style="margin-bottom:8px">Delete your PetNabor account</h2>
      <p>We received a request to delete the PetNabor account for
         <b>{escape(user.email)}</b>.</p>
      <p>To continue, open this link within 24 hours and confirm:</p>
      <p style="margin:24px 0">
        <a href="{link}" style="background:#E05A1B;color:#fff;padding:12px 20px;
           border-radius:8px;text-decoration:none;font-weight:bold">Review and delete my account</a>
      </p>
      <p style="color:#5B6670;font-size:13px">If you didn't ask for this, ignore this email —
         nothing will happen to your account.</p>
    </div>
    """
    EmailService.send_html_email("Confirm deleting your PetNabor account", html, [user.email])


@require_http_methods(["GET", "POST"])
def request_deletion(request):
    if request.method == "GET":
        return render(request, TEMPLATE, {"step": "request"})

    email = (request.POST.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return render(
            request, TEMPLATE, {"step": "request", "error": "Please enter a valid email address."}
        )

    if _over_limit(f"ip:{_client_ip(request)}") or _over_limit(f"email:{email}"):
        return render(
            request,
            TEMPLATE,
            {"step": "request", "error": "Too many requests. Please try again in an hour."},
        )

    user = User.objects.filter(email__iexact=email, is_active=True).first()
    if user and not (user.is_staff or user.is_superuser):
        _send_link(request, user)

    # Same answer either way (doesn't reveal whether the email has an account).
    return render(request, TEMPLATE, {"step": "sent", "email": email})


def _user_from_token(token):
    try:
        data = signing.loads(token or "", salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
    except signing.BadSignature:  # includes SignatureExpired
        return None
    return User.objects.filter(pk=data.get("u"), is_active=True).first()


@require_http_methods(["GET", "POST"])
def confirm_deletion(request):
    token = request.GET.get("token") or request.POST.get("token")
    user = _user_from_token(token)
    if user is None or user.is_staff or user.is_superuser:
        return render(request, TEMPLATE, {"step": "invalid"})

    if request.method == "GET":
        return render(request, TEMPLATE, {"step": "confirm", "token": token, "email": user.email})

    try:
        delete_account(user)
    except AccountDeletionBlocked as exc:
        return render(request, TEMPLATE, {"step": "blocked", "reason": str(exc.detail)})
    except Exception:
        logger.exception("Web account deletion failed for %s", user.pk)
        return render(request, TEMPLATE, {"step": "error"})

    return render(request, TEMPLATE, {"step": "done"})
