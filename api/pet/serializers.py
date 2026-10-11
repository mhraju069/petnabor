import json

from rest_framework import serializers

from .models import TEMPERAMENT_TAGS, PetProfile


class TemperamentField(serializers.ListField):
    """
    List of temperament tags. Multipart uploads (pet photo/document) can't
    send lists, so a JSON string or comma-separated string is accepted too.
    """

    child = serializers.ChoiceField(choices=TEMPERAMENT_TAGS)

    def get_value(self, dictionary):
        # QueryDict (multipart) would otherwise only hand us a list via getlist.
        if hasattr(dictionary, "getlist") and self.field_name in dictionary:
            values = dictionary.getlist(self.field_name)
            return values[0] if len(values) == 1 else values
        return super().get_value(dictionary)

    def to_internal_value(self, data):
        if isinstance(data, str):
            text = data.strip()
            if not text:
                data = []
            elif text.startswith("["):
                try:
                    data = json.loads(text)
                except ValueError:
                    raise serializers.ValidationError("Invalid temperament list.")
            else:
                data = [t.strip() for t in text.split(",") if t.strip()]
        values = super().to_internal_value(data)
        # De-duplicate, keep order.
        return list(dict.fromkeys(values))


class PetProfileSerializer(serializers.ModelSerializer):
    temperament = TemperamentField(required=False)

    class Meta:
        model = PetProfile
        fields = [
            "id",
            "user",
            "pet_name",
            "pet_type",
            "breed",
            "gender",
            "is_spayed_neutered",
            "temperament",
            "size",
            "weight",
            "weight_type",
            "date_of_birth",
            "vaccination_status",
            "vaccination_document",
            "vet_contact_number",
            "image",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ('id', 'user', 'created_at', 'updated_at')
