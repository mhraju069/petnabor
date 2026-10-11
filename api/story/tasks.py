import logging

from celery import shared_task
from django.utils import timezone

from api.media_utils import compress_image_to_webp
from .models import Story, StoryMediaTypeChoices

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def process_story_media_task(self, story_id: str):
    """Compress story image media asynchronously and delete replaced old media."""
    try:
        story = Story.objects.get(id=story_id)
    except Story.DoesNotExist:
        logger.error("Story %s not found for media processing.", story_id)
        return

    if story.media_type != StoryMediaTypeChoices.IMAGE or not story.media:
        return

    old_name = story.media.name
    compressed = compress_image_to_webp(story.media)
    if not compressed:
        return

    try:
        story.media.save(compressed.name, compressed, save=False)
        story.save(update_fields=["media"])

        if old_name and old_name != story.media.name:
            story.media.storage.delete(old_name)
    except Exception as exc:
        logger.exception("Failed to process story media for %s: %s", story_id, exc)
        raise self.retry(exc=exc)


@shared_task
def delete_expired_stories(batch_size: int = 200):
    """
    Hard-delete expired stories. The post_delete signal on Story removes each
    story's media from storage (Cloudinary). Runs in batches; re-queues itself
    if more remain.
    """
    ids = list(
        Story.objects.filter(expires_at__lte=timezone.now())
        .values_list("id", flat=True)[:batch_size]
    )
    for story in Story.objects.filter(id__in=ids):
        story.delete()  # per-instance so post_delete fires with the file name

    if len(ids) == batch_size:
        delete_expired_stories.delay(batch_size)
    return len(ids)
