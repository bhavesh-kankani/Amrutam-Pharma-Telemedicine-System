from django.contrib import admin
from .models import AvailabilitySlot, Consultation, Prescription, Payment


@admin.register(AvailabilitySlot)
class AvailabilitySlotAdmin(admin.ModelAdmin):
    list_display = (
        "doctor",
        "start_time",
        "end_time",
        "status",
        "held_by",
        "hold_expires_at",
    )
    list_filter = ("status", "doctor__specialization")
    search_fields = ("doctor__license_number", "doctor__user__email", "held_by__email")
    readonly_fields = ("hold_expires_at",)


@admin.register(Consultation)
class ConsultationAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "doctor", "slot", "status")
    list_filter = ("status",)
    search_fields = ("patient__email", "doctor__license_number", "meeting_link")


@admin.register(Prescription)
class PrescriptionAdmin(admin.ModelAdmin):
    list_display = ("id", "consultation", "issued_at")
    readonly_fields = ("issued_at",)
    search_fields = ("consultation__id", "notes")

    def has_change_permission(self, request, obj=None):
        # Prescriptions are immutable once created
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "consultation",
        "amount",
        "status",
        "idempotency_key",
        "transaction_reference_number",
    )
    list_filter = ("status",)
    search_fields = (
        "idempotency_key",
        "transaction_reference_number",
        "consultation__id",
    )
    readonly_fields = ("idempotency_key",)
