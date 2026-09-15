import uuid
from django.db import models
from django.utils import timezone
from django.conf import settings
from django.core.exceptions import ValidationError


class AvailabilitySlotStatus(models.TextChoices):
    AVAILABLE = "available", "Available"
    HOLD = "hold", "Hold"
    LOCKED = "locked", "Locked"
    BOOKED = "booked", "Booked"


class AvailabilitySlot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    doctor = models.ForeignKey(
        "accounts.Doctor",
        on_delete=models.CASCADE,
        related_name="availability_slots",
        db_column="doctor_id",
        db_index=True,
    )
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=AvailabilitySlotStatus.choices,
        default=AvailabilitySlotStatus.AVAILABLE,
    )
    held_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="held_slots",
        db_column="held_by_id",
        db_index=True,
    )
    hold_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "availability_slots"
        verbose_name = "Availability Slot"
        verbose_name_plural = "Availability Slots"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="slot_end_time_after_start_time",
            ),
        ]
        indexes = [
            models.Index(fields=["doctor", "start_time", "status"]),
            models.Index(fields=["status", "hold_expires_at"]),
        ]

    @property
    def is_actively_held(self) -> bool:
        """Returns True if the slot is in HOLD/LOCKED status and within the 5-minute window."""
        if self.status in (AvailabilitySlotStatus.HOLD, AvailabilitySlotStatus.LOCKED):
            if self.hold_expires_at and self.hold_expires_at > timezone.now():
                return True
        return False

    @property
    def display_status(self) -> str:
        """
        Self-healing dynamic status for clients.
        - BOOKED -> 'BOOKED'
        - Active HOLD -> 'UNDER_BOOKING'
        - Expired HOLD or AVAILABLE -> 'AVAILABLE'
        """
        if self.status == AvailabilitySlotStatus.BOOKED:
            return "BOOKED"
        if self.is_actively_held:
            return "UNDER_BOOKING"
        return "AVAILABLE"

    @property
    def remaining_seconds(self) -> int | None:
        """Returns remaining hold time in seconds if actively held, otherwise None."""
        if self.is_actively_held and self.hold_expires_at:
            delta = int((self.hold_expires_at - timezone.now()).total_seconds())
            return max(0, delta)
        return None

    def __str__(self):
        return f"Slot {self.doctor.license_number}: {self.start_time} - {self.end_time} ({self.display_status})"


class ConsultationStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class Consultation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="patient_consultations",
        db_column="patient_id",
        db_index=True,
    )
    doctor = models.ForeignKey(
        "accounts.Doctor",
        on_delete=models.CASCADE,
        related_name="doctor_consultations",
        db_column="doctor_id",
        db_index=True,
    )
    slot = models.OneToOneField(
        AvailabilitySlot,
        on_delete=models.PROTECT,
        related_name="consultation",
        db_column="slot_id",
        unique=True,
    )
    status = models.CharField(
        max_length=20,
        choices=ConsultationStatus.choices,
        default=ConsultationStatus.SCHEDULED,
    )
    meeting_link = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "consultations"
        verbose_name = "Consultation"
        verbose_name_plural = "Consultations"

    def __str__(self):
        return f"Consultation {self.id} - {self.patient.email} with {self.doctor.specialization} ({self.status})"


def default_medications_list():
    return []


class Prescription(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    consultation = models.ForeignKey(
        Consultation,
        on_delete=models.CASCADE,
        related_name="prescriptions",
        db_column="consultation_id",
    )
    medications = models.JSONField(default=default_medications_list)
    notes = models.TextField(blank=True, default="")
    issued_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "prescriptions"
        verbose_name = "Prescription"
        verbose_name_plural = "Prescriptions"

    def clean(self):
        super().clean()
        # Immutable check: cannot be altered if already saved in DB
        if self.pk and Prescription.objects.filter(pk=self.pk).exists():
            raise ValidationError("Prescriptions are immutable legal medical records and cannot be modified.")

    def save(self, *args, **kwargs):
        # Enforce immutability
        if self.pk and Prescription.objects.filter(pk=self.pk).exists():
            raise ValidationError("Prescriptions are immutable legal medical records and cannot be modified.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Prescription {self.id} for Consultation {self.consultation_id}"


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    REFUNDED = "refunded", "Refunded"


class Payment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    consultation = models.ForeignKey(
        Consultation,
        on_delete=models.CASCADE,
        related_name="payments",
        db_column="consultation_id",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
    )
    idempotency_key = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
    )
    transaction_reference_number = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text="UPI Reference Number / Bank UTR / Gateway Payment ID",
    )

    class Meta:
        db_table = "payments"
        verbose_name = "Payment"
        verbose_name_plural = "Payments"

    def __str__(self):
        return f"Payment {self.id} - ${self.amount} ({self.status})"
