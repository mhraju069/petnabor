import logging
from django.db import transaction
from django.db.models import F
from django.core.cache import cache
from api.notifications.services import send_notification
from api.notifications.models import NotificationTypes
from .models import Blog, BlogLike, BlogViewTracker, BlogComment

logger = logging.getLogger(__name__)


class BlogService:
    """Service class to encapsulate complex business logic for Blogs."""

    @staticmethod
    def toggle_like(blog: Blog, user) -> bool:
        """
        Toggles like for a user on a blog.
        Returns True if liked, False if unliked.
        Uses F() expressions to atomically update counter.
        """
        with transaction.atomic():
            like, created = BlogLike.objects.get_or_create(blog=blog, user=user)
            
            if not created:
                like.delete()
                Blog.objects.filter(id=blog.id).update(likes_count=F('likes_count') - 1)
                cache.delete("blog_list_cache")  # invalidate cache
                return False
            else:
                Blog.objects.filter(id=blog.id).update(likes_count=F('likes_count') + 1)
                cache.delete("blog_list_cache")
                cache.delete("blog_popular_cache")
                
                # Notify only on the first like ever; re-likes stay silent.
                if blog.author != user and cache.add(
                    f"notified_blog_like_{blog.id}_{user.id}", 1, timeout=60 * 60 * 24 * 365
                ):
                    sender_display = f"{user.first_name} {user.last_name}".strip() or user.username or "Someone"
                    send_notification(
                        title=sender_display,
                        body=f"liked your blog '{blog.title}'.",
                        user_id=blog.author.id,
                        notification_type=NotificationTypes.LIKE,
                        data={"blog_slug": blog.slug}
                    )
                
                return True

    @staticmethod
    def track_view_sync(blog: Blog, ip_address: str, user=None):
        """
        Synchronous fallback for view tracking if you want to write directly to DB.
        For production, a celery task would be better: track_view_async.delay(blog.id, ip, user.id)
        This prevents duplicate view increments per IP.
        """
        try:
            # Simple bot exclusion
            if not ip_address:
                return

            view_exists_query = BlogViewTracker.objects.filter(blog=blog)
            if user and user.is_authenticated:
                view_exists_query = view_exists_query.filter(user=user)
            else:
                view_exists_query = view_exists_query.filter(ip_address=ip_address)

            if not view_exists_query.exists():
                with transaction.atomic():
                    BlogViewTracker.objects.create(blog=blog, ip_address=ip_address, user=user if user and user.is_authenticated else None)
                    Blog.objects.filter(id=blog.id).update(views_count=F('views_count') + 1)
        except Exception as e:
            logger.error(f"Error tracking blog view for blog {blog.id}: {e}", exc_info=True)

    @staticmethod
    def create_comment(blog: Blog, user, text: str, parent_id=None, media=None) -> BlogComment:
        """
        Creates a new comment or reply and atomically updates counters.
        """
        with transaction.atomic():
            parent_comment = None
            if parent_id:
                try:
                    parent_comment = BlogComment.objects.select_for_update().get(id=parent_id, blog=blog, is_deleted=False)
                except BlogComment.DoesNotExist:
                    raise ValueError("Parent comment does not exist or is deleted.")

            comment = BlogComment.objects.create(
                blog=blog,
                user=user,
                parent_comment=parent_comment,
                comment_text=text,
                media_file=media
            )

            if comment.media_file:
                from .tasks import process_blog_comment_media_task

                process_blog_comment_media_task.delay(str(comment.id))

            # Update blog comment count
            Blog.objects.filter(id=blog.id).update(comments_count=F('comments_count') + 1)
            
            # If reply, update parent reply count
            if parent_comment:
                BlogComment.objects.filter(id=parent_comment.id).update(replies_count=F('replies_count') + 1)

            # Trigger notifications
            if parent_comment and parent_comment.user != user:
                sender_display = f"{user.first_name} {user.last_name}".strip() or user.username or "Someone"
                send_notification(
                    title=sender_display,
                    body=f"replied to your comment on '{blog.title}'.",
                    user_id=parent_comment.user.id,
                    notification_type=NotificationTypes.COMMENT,
                    data={"blog_slug": blog.slug}
                )
            elif blog.author != user:
                sender_display = f"{user.first_name} {user.last_name}".strip() or user.username or "Someone"
                send_notification(
                    title=sender_display,
                    body=f"commented on your blog '{blog.title}'.",
                    user_id=blog.author.id,
                    notification_type=NotificationTypes.COMMENT,
                    data={"blog_slug": blog.slug}
                )
            
            return comment

    @staticmethod
    def soft_delete_comment(comment_id: str, user) -> bool:
        """
        Soft deletes a comment if the user is the owner, updates counter.
        """
        with transaction.atomic():
            try:
                comment = BlogComment.objects.get(id=comment_id, user=user, is_deleted=False)
                comment.is_deleted = True
                comment.save(update_fields=['is_deleted'])
                
                # decrement blog comment count
                Blog.objects.filter(id=comment.blog_id).update(comments_count=F('comments_count') - 1)
                
                # decrement parent reply count
                if comment.parent_comment_id:
                     BlogComment.objects.filter(id=comment.parent_comment_id).update(replies_count=F('replies_count') - 1)
                
                return True
            except BlogComment.DoesNotExist:
                return False
