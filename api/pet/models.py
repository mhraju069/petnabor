from django.db import models
import uuid
from django.conf import settings
import os



def pet_image_path(instance, filename):
    ext = filename.split('.')[-1]
    filename = f"pet_{uuid.uuid4()}.{ext}"
    return os.path.join('pets/images/', filename)

class WeightTypes(models.TextChoices):
    KG = 'kg', 'Kilograms'
    LB = 'lb', 'Pounds'


class PetGender(models.TextChoices):
    MALE = 'male', 'Male'
    FEMALE = 'female', 'Female'


# Tags a pet owner can pick to describe temperament (validated in the serializer).
TEMPERAMENT_TAGS = (
    "friendly_with_dogs",
    "friendly_with_cats",
    "good_with_kids",
    "playful",
    "calm",
    "energetic",
    "shy",
    "anxious",
    "house_trained",
    "leash_trained",
)

class PetProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='pets')
    
    pet_name = models.CharField(max_length=100)
    pet_type = models.CharField(max_length=50)  # "Dog" / "Cat"
    breed = models.CharField(max_length=100, blank=True, default="")
    gender = models.CharField(max_length=10, choices=PetGender.choices, blank=True, default="")
    # None = owner hasn't said.
    is_spayed_neutered = models.BooleanField(null=True, blank=True)
    temperament = models.JSONField(default=list, blank=True)
    size = models.CharField(max_length=50) 
    weight = models.DecimalField(max_digits=5, decimal_places=2)
    weight_type = models.CharField(max_length=10, choices=WeightTypes.choices, default=WeightTypes.KG)
    date_of_birth = models.DateField()
    
    vaccination_status = models.CharField(max_length=100)
    vaccination_document = models.FileField(upload_to='pets/docs/', null=True, blank=True)
    vet_contact_number = models.CharField(max_length=15, null=True, blank=True)
    image = models.ImageField(upload_to=pet_image_path, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.pet_name
