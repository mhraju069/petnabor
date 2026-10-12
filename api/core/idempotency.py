"""
Idempotent create for slow uploads (stories, posts).

The app sends an `Idempotency-Key` header that stays the same across retries
of one upload. If a request times out on the phone but finishes on the
server, the retry gets the already-created object back instead of creating
a duplicate.

Usage in a view:

    with idempotent_create(request, "story") as idem:
        if idem.existing_id:
            return <200 with the existing object>
        obj = create(...)
        idem.remember(obj.id)
"""

from contextlib import contextmanager

from django.core.cache import cache
from rest_framework.exceptions import APIException

RESULT_TTL = 60 * 60 * 24  # how long a finished key maps to its object
PENDING_TTL = 60 * 10  # an in-flight upload holds its key at most this long
_PENDING = "__pending__"


class UploadInProgress(APIException):
    status_code = 409
    default_detail = "This upload is still being processed. Please try again in a moment."
    default_code = "upload_in_progress"


class _Idem:
    def __init__(self, cache_key):
        self.cache_key = cache_key
        self.existing_id = None
        self._remembered = False

    def remember(self, object_id):
        if self.cache_key:
            cache.set(self.cache_key, str(object_id), RESULT_TTL)
        self._remembered = True


def _header_key(request):
    key = (request.headers.get("Idempotency-Key") or "").strip()
    return key[:100] or None


@contextmanager
def idempotent_create(request, scope):
    key = _header_key(request)
    if not key:
        yield _Idem(None)
        return

    cache_key = f"idem:{scope}:{request.user.id}:{key}"
    idem = _Idem(cache_key)

    # cache.add is atomic: only the first request claims the key.
    if not cache.add(cache_key, _PENDING, PENDING_TTL):
        value = cache.get(cache_key)
        if value == _PENDING:
            raise UploadInProgress()
        if value:
            idem.existing_id = value
            yield idem
            return
        # Expired between add() and get(): claim it now.
        cache.set(cache_key, _PENDING, PENDING_TTL)

    try:
        yield idem
    except BaseException:
        # Let the client retry a failed create.
        cache.delete(cache_key)
        raise
    if not idem._remembered:
        cache.delete(cache_key)
