from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("meeting", "0002_alter_meeting_reason"),
    ]

    operations = [
        migrations.AddField(
            model_name="meeting",
            name="hidden_for",
            field=models.ManyToManyField(blank=True, related_name="hidden_meetings", to=settings.AUTH_USER_MODEL),
        ),
    ]
