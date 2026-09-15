from rest_framework import serializers
from accounts.models import Doctor
from .models import AvailabilitySlot, Consultation, Prescription, Payment


class DoctorSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = Doctor
        fields = (
            "id",
            "email",
            "specialization",
            "license_number",
            "consultation_fee",
            "is_verified",
        )


class AvailabilitySlotSerializer(serializers.ModelSerializer):
    doctor_email = serializers.EmailField(source="doctor.user.email", read_only=True)
    display_status = serializers.CharField(read_only=True)
    remaining_seconds = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = AvailabilitySlot
        fields = (
            "id",
            "doctor",
            "doctor_email",
            "start_time",
            "end_time",
            "status",
            "display_status",
            "remaining_seconds",
        )


class HoldSlotSerializer(serializers.Serializer):
    slot_id = serializers.UUIDField()


class ConfirmBookingSerializer(serializers.Serializer):
    slot_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    idempotency_key = serializers.CharField(max_length=255)
    payment_status = serializers.ChoiceField(
        choices=["success", "failed", "refunded"],
        default="success",
        required=False,
    )
    transaction_reference_number = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        allow_null=True,
    )


class PaymentWebhookSerializer(serializers.Serializer):
    event = serializers.ChoiceField(choices=["payment.success", "payment.failed", "payment.refunded"])
    idempotency_key = serializers.CharField(max_length=255)
    slot_id = serializers.UUIDField(required=False)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    failure_reason = serializers.CharField(required=False, allow_blank=True)


class PrescriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Prescription
        fields = ("id", "consultation", "medications", "notes", "issued_at")
        read_only_fields = ("id", "issued_at")


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = (
            "id",
            "consultation",
            "amount",
            "status",
            "idempotency_key",
            "transaction_reference_number",
        )
        read_only_fields = ("id",)


class ConsultationDetailSerializer(serializers.ModelSerializer):
    prescriptions = PrescriptionSerializer(many=True, read_only=True)

    class Meta:
        model = Consultation
        fields = (
            "id",
            "patient",
            "doctor",
            "slot",
            "status",
            "meeting_link",
            "prescriptions",
        )


# Aliases for backwards compatibility
ConsultationSerializer = ConsultationDetailSerializer
LockSlotRequestSerializer = HoldSlotSerializer
BookSlotRequestSerializer = ConfirmBookingSerializer
