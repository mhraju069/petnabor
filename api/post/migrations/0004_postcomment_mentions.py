from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("post", "0003_add_processing_status_medium_file_soft_delete"),
    ]

    operations = [
        migrations.AddField(
            model_name="postcomment",
            name="mentions",
            field=models.ManyToManyField(
                blank=True, related_name="comment_mentions", to=settings.AUTH_USER_MODEL
            ),
        ),
    ]
