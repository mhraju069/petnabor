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
    # Held tips: refund after HELD_TIP_EXPIRY_DAYS, cancel abandoned unpaid ones.
    "expire-held-tips": {
        "task": "api.tip.tasks.expire_held_tips",
        "schedule": 6 * 60 * 60,  # every 6 hours
    },
}
