from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0003_message_media_file"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="message",
            index=models.Index(
                condition=Q(is_read=False, is_deleted_for_everyone=False),
                fields=["thread", "sender"],
                name="msg_unread_by_thread",
            ),
        ),
    ]
