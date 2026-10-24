"""
Rename the member role petsitter -> petpal (shown as "PetPal").

Older app builds still send "petsitter"; users/roles.py maps it to "petpal".
"""

from django.db import migrations, models


def forwards(apps, schema_editor):
    User = apps.get_model("users", "User")
    User.objects.filter(user_type="petsitter").update(user_type="petpal")


def backwards(apps, schema_editor):
    User = apps.get_model("users", "User")
    User.objects.filter(user_type="petpal").update(user_type="petsitter")


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0014_user_type_pet_parent_pet_sitter"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="user_type",
            field=models.CharField(
                choices=[
                    ("petparent", "Pet Parent"),
                    ("petpal", "PetPal"),
                    ("vendor", "Vendor"),
                    ("admin", "Admin"),
                ],
                default="petparent",
                max_length=20,
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
