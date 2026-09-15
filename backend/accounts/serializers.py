from rest_framework import serializers
from .models import User, Profile, Doctor


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = [
            "id",
            "first_name",
            "last_name",
            "phone_number",
            "date_of_birth",
        ]


class DoctorSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = Doctor
        fields = [
            "id",
            "user",
            "email",
            "specialization",
            "license_number",
            "consultation_fee",
            "is_verified",
        ]
        read_only_fields = ["is_verified"]


class UserSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer(read_only=True)
    doctor_profile = DoctorSerializer(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "role",
            "mfa_enabled",
            "created_at",
            "profile",
            "doctor_profile",
        ]
        read_only_fields = ["id", "created_at"]


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["id", "email", "password", "role"]

    def create(self, validated_data):
        user = User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            role=validated_data.get("role", User.Role.PATIENT),
        )
        return user
