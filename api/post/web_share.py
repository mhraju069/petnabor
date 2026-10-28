"""
GET /post/<id> — the page a shared post link opens when the app can't.

With the app installed, the OS opens the app straight to the post instead
(api/core/app_links.py). Otherwise this page shows a preview (public posts
only: friends-only posts, deleted posts and hidden accounts get a plain
"open in the app" page), an "Open in PetNabor" button and store links.
The preview also feeds link cards in WhatsApp, Messenger, iMessage, etc.
"""

from django.conf import settings
from django.shortcuts import render

from api.post.models import MediaProcessingStatus, Post, PrivacyChoices

CAPTION_PREVIEW = 200


def _author_name(user):
    name = " ".join(filter(None, [user.first_name, user.last_name])).strip()
    return name or "A PetNabor member"


def _preview_image(request, post):
    media = (
        post.media.filter(processing_status=MediaProcessingStatus.DONE)
        .order_by("order")
        .first()
    )
    if media is None:
        return None
    file = media.medium_file or media.thumbnail_file
    if not file and media.media_type == "IMAGE":
        file = media.file
    return request.build_absolute_uri(file.url) if file else None


def post_share_page(request, post_id):
    post = (
        Post.objects.select_related("author")
        .filter(
            id=post_id,
            is_deleted=False,
            privacy=PrivacyChoices.PUBLIC,
            author__is_active=True,
            author__is_deactivated=False,
        )
        .first()
    )
    context = {
        "post_id": post_id,
        "app_url": f"petnabor://post/{post_id}",
        "page_url": request.build_absolute_uri(),
        "app_store_url": settings.APP_STORE_URL,
        "play_store_url": settings.PLAY_STORE_URL,
        "post": None,
    }
    if post is not None:
        caption = (post.content_text or "").strip()
        if len(caption) > CAPTION_PREVIEW:
            caption = caption[:CAPTION_PREVIEW].rstrip() + "…"
        context["post"] = {
            "author": _author_name(post.author),
            "caption": caption,
            "image": _preview_image(request, post),
            "likes": post.likes_count,
            "comments": post.comments_count,
        }
    return render(request, "web/shared_post.html", context)
