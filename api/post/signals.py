"""Live "new post" events (see tasks.announce_new_post)."""

import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Post

logger = logging.getLogger(__name__)


def _queue(post_id: str) -> None:
    from .tasks import announce_new_post

    try:
        announce_new_post.delay(post_id)
    except Exception:
        # The post is saved; a missed event only delays the "new posts" hint.
        logger.exception("Couldn't queue the new-post event for %s", post_id)


@receiver(post_save, sender=Post, dispatch_uid="announce_new_post")
def announce_new_post_on_create(sender, instance, created, raw=False, **kwargs):
    if not created or raw:
        return
    post_id = str(instance.pk)
    # After commit: the post must be readable by the time apps come asking.
    transaction.on_commit(lambda: _queue(post_id))
