"""
Report model moved from api.post to its own dedicated app.
"""

import uuid
from django.conf import settings
from django.db import models


class ReportTargetTypeChoices(models.TextChoices):
    USER = "USER", "User"
    POST = "POST", "Post"
    COMMENT = "COMMENT", "Comment"
    STORY = "STORY", "Story"
    BLOG = "BLOG", "Blog"
    MEETING = "MEETING", "Meeting"
    MESSAGE = "MESSAGE", "Chat message"


class Report(models.Model):
    """Generic reporting model for Posts, Users, Comments, etc."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        related_name='reports_submitted', 
        on_delete=models.CASCADE
    )
    
    target_type = models.CharField(max_length=20, choices=ReportTargetTypeChoices.choices)
    target_id = models.UUIDField(db_index=True)
    
    reason = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    
    is_resolved = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['target_type', 'target_id']),
            models.Index(fields=['is_resolved']),
        ]

    def __str__(self):
        return f"Report by {self.reporter} on {self.target_type} {self.target_id}"
