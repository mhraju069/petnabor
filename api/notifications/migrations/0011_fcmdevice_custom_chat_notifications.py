from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0010_add_tip_sent_notification_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="fcmdevice",
            name="custom_chat_notifications",
            field=models.BooleanField(default=False),
        ),
    ]
