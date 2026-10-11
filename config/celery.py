"""
Celery configuration for the PetNabor project.
"""
import os
from celery import Celery


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('config')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
app.conf.beat_schedule = {
    "delete-expired-stories": {
        "task": "api.story.tasks.delete_expired_stories",
        "schedule": 15 * 60,  # every 15 minutes
    },
}
