"""
Add breed, gender, spayed/neutered and temperament to pets.

Older app builds stored the breed in `pet_type` (from the edit screen). Rows
whose pet_type isn't "Dog"/"Cat" are moved: breed = old pet_type, and
pet_type is left as-is because we can't reliably tell dog from cat breeds.
"""

from django.db import migrations, models


def move_breed_out_of_pet_type(apps, schema_editor):
    PetProfile = apps.get_model("pet", "PetProfile")
    for pet in PetProfile.objects.exclude(pet_type__iexact="dog").exclude(
        pet_type__iexact="cat"
    ):
        if pet.pet_type and not pet.breed:
            pet.breed = pet.pet_type
            pet.save(update_fields=["breed"])


class Migration(migrations.Migration):

    dependencies = [
        ("pet", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="petprofile",
            name="breed",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="petprofile",
            name="gender",
            field=models.CharField(
                blank=True,
                choices=[("male", "Male"), ("female", "Female")],
                default="",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="petprofile",
            name="is_spayed_neutered",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="petprofile",
            name="temperament",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(move_breed_out_of_pet_type, migrations.RunPython.noop),
    ]
