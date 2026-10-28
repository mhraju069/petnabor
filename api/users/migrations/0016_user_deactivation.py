from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0015_user_type_petpal"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="is_deactivated",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="user",
            name="deactivated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
