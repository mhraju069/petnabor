import uuid
from django.db import models
from django.conf import settings

User = settings.AUTH_USER_MODEL

class Meeting(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('ACCEPTED', 'Accepted'),
        ('CANCELLED', 'Cancelled'),
        ('COMPLETED', 'Completed'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sender = models.ForeignKey(User, related_name='meetings_sent', on_delete=models.CASCADE)
    receiver = models.ForeignKey(User, related_name='meetings_received', on_delete=models.CASCADE)
    
    visitor_name = models.CharField(max_length=255)
    visitor_phone = models.CharField(max_length=20)
    visit_date = models.DateField()
    visit_time = models.TimeField()
    reason = models.CharField(max_length=50)
    
    address_street = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    zipcode = models.CharField(max_length=20)
    message = models.TextField(blank=True, null=True)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')

    # People who removed this meeting from their own history (Previous tab).
    # Per person: the other participant keeps it, and reviews stay.
    hidden_for = models.ManyToManyField(User, related_name='hidden_meetings', blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-visit_date', '-visit_time']

    def __str__(self):
        return f"Meeting between {self.sender} and {self.receiver} on {self.visit_date}"


class MeetingFeedback(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    meeting = models.ForeignKey(Meeting, related_name='feedbacks', on_delete=models.CASCADE)
    reviewer = models.ForeignKey(User, related_name='given_meeting_feedbacks', on_delete=models.CASCADE)
    reviewee = models.ForeignKey(User, related_name='received_meeting_feedbacks', on_delete=models.CASCADE)
    
    rating = models.FloatField(null=True, blank=True)
    feedback_text = models.TextField()
    is_public = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Feedback by {self.reviewer} for Meeting {self.meeting.id}"
