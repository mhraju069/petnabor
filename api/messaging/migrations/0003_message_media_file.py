import api.messaging.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0002_message_story_reply"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="media_file",
            field=models.FileField(
                blank=True,
                max_length=500,
                null=True,
                upload_to=api.messaging.models.chat_media_path,
            ),
        ),
    ]
