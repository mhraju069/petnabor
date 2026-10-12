from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="message",
            name="message_type",
            field=models.CharField(
                choices=[
                    ("TEXT", "Text"),
                    ("IMAGE", "Image"),
                    ("VIDEO", "Video"),
                    ("AUDIO", "Audio"),
                    ("FILE", "File"),
                    ("SYSTEM", "System"),
                    ("STORY_REPLY", "Story reply"),
                ],
                db_index=True,
                default="TEXT",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="message",
            name="metadata",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
