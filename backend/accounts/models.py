import uuid
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.utils import timezone
from django.conf import settings
from cryptography.fernet import Fernet, InvalidToken

from .validators import normalize_and_validate_phone


class EncryptedCharField(models.CharField):
    """
    CharField that encrypts PII at rest using AES (Fernet) before writing to DB
    and decrypts data when reading from DB.
    """
    def __init__(self, *args, **kwargs):
        # Fernet ciphertext tokens require up to ~255 characters
        kwargs.setdefault("max_length", 255)
        super().__init__(*args, **kwargs)

    def _get_fernet(self):
        key = getattr(settings, "FIELD_ENCRYPTION_KEY", None)
        if not key:
            # Fallback dev key
            key = b"mAixxkZzYg_ZfTFXKmHoDvfm86eDzAkILeQpErsrxkw="
        elif isinstance(key, str):
            key = key.encode()
        return Fernet(key)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None or value == "":
            return value
        fernet = self._get_fernet()
        try:
            # If already ciphertext, leave as-is
            fernet.decrypt(value.encode())
            return value
        except (InvalidToken, Exception):
            return fernet.encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        fernet = self._get_fernet()
        try:
            return fernet.decrypt(value.encode()).decode()
        except (InvalidToken, Exception):
            return value


class Role(models.TextChoices):
    PATIENT = "patient", "Patient"
    DOCTOR = "doctor", "Doctor"
    ADMIN = "admin", "Admin"


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, role=Role.PATIENT, **extra_fields):
        if not email:
            raise ValueError("The Email field must be set")
        email = self.normalize_email(email)
        user = self.model(email=email, role=role, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", Role.ADMIN)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(max_length=255, unique=True, db_index=True)
    password = models.CharField(max_length=255, db_column="password_hash")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.PATIENT)
    mfa_enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["role"]

    class Meta:
        db_table = "users"
        verbose_name = "User"
        verbose_name_plural = "Users"

    def __str__(self):
        return f"{self.email} ({self.role})"


class Profile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        db_column="user_id",
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone_number = EncryptedCharField(
        max_length=255,
        blank=True,
        null=True,
        validators=[normalize_and_validate_phone],
    )
    date_of_birth = models.DateField(blank=True, null=True)

    class Meta:
        db_table = "profiles"
        verbose_name = "Profile"
        verbose_name_plural = "Profiles"

    def clean(self):
        super().clean()
        if self.phone_number:
            self.phone_number = normalize_and_validate_phone(self.phone_number)

    def save(self, *args, **kwargs):
        if self.phone_number:
            self.phone_number = normalize_and_validate_phone(self.phone_number)
        self.full_clean()  # Ensures clean() and validators run before saving to DB
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class Doctor(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="doctor_profile",
        db_column="user_id",
    )
    specialization = models.CharField(max_length=100, db_index=True)
    license_number = models.CharField(max_length=100, unique=True)
    consultation_fee = models.DecimalField(max_digits=10, decimal_places=2)
    is_verified = models.BooleanField(default=False)

    class Meta:
        db_table = "doctors"
        verbose_name = "Doctor"
        verbose_name_plural = "Doctors"

    def __str__(self):
        return f"Dr. ({self.specialization}) - {self.user.email}"
