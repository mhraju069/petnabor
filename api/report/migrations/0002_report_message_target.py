from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("report", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="report",
            name="target_type",
            field=models.CharField(
                choices=[
                    ("USER", "User"),
                    ("POST", "Post"),
                    ("COMMENT", "Comment"),
                    ("STORY", "Story"),
                    ("BLOG", "Blog"),
                    ("MEETING", "Meeting"),
                    ("MESSAGE", "Chat message"),
                ],
                max_length=20,
            ),
        ),
    ]
