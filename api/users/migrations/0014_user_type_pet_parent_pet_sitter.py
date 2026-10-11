"""
Rename member user types: petnabor -> petparent, petpal -> petsitter.

Also fixes rows saved with the old "patnabor"/"patpal" typos. Accounts
already stored as "petparent"/"petsitter" (written by an app build whose
values were never valid choices) are now valid and are left untouched.
"""

from django.db import migrations, models

FORWARD = {
    "petnabor": "petparent",
    "patnabor": "petparent",
    "petpal": "petsitter",
    "patpal": "petsitter",
}
BACKWARD = {"petparent": "petnabor", "petsitter": "petpal"}


def _remap(apps, mapping):
    User = apps.get_model("users", "User")
    for old, new in mapping.items():
        User.objects.filter(user_type=old).update(user_type=new)


def forwards(apps, schema_editor):
    _remap(apps, FORWARD)


def backwards(apps, schema_editor):
    _remap(apps, BACKWARD)


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0013_user_ai_data_consent"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="user_type",
            field=models.CharField(
                choices=[
                    ("petparent", "Pet Parent"),
                    ("petsitter", "Pet Sitter"),
                    ("vendor", "Vendor"),
                    ("admin", "Admin"),
                ],
                default="petparent",
                max_length=20,
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
