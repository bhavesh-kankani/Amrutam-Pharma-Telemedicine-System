import uuid
from decimal import Decimal
from django.db import transaction
from rest_framework.exceptions import ValidationError
from .models import (
    AvailabilitySlot,
    AvailabilitySlotStatus,
    Consultation,
    ConsultationStatus,
    Payment,
    PaymentStatus,
)
from .tasks import release_expired_slot_hold


class ConcurrencyLockError(Exception):
    """Raised when an optimistic lock conflict occurs."""


class SlotUnavailableError(ConcurrencyLockError):
    """Raised when a slot is already locked or booked."""


class IdempotencyConflictError(Exception):
    """Raised when an idempotency key is reused with different parameters."""


from datetime import timedelta
from django.utils import timezone


class BookingService:
    """
    Handles explicit temporary slot holds, ownership verification, and booking workflows.
    """

    @staticmethod
    @transaction.atomic
    def hold_slot(slot_id: str, caller_user=None, patient=None) -> AvailabilitySlot:
        """
        Atomically places a 5-minute hold on an available or expired slot.
        Transitions status: 'available' -> 'hold'.
        Binds held_by = user and hold_expires_at = now + 5 minutes.
        Enqueues auto-release Celery task (countdown=300).
        """
        user = caller_user if caller_user is not None else patient
        try:
            slot = (
                AvailabilitySlot.objects.select_for_update(nowait=False)
                .select_related("doctor__user")
                .get(id=slot_id)
            )
        except AvailabilitySlot.DoesNotExist:
            raise ValidationError("Availability slot not found.")

        # Domain Guard: Prevent doctor from holding their own slot
        if slot.doctor.user == user:
            raise ValidationError("Doctors cannot book consultations with themselves.")

        if slot.status == AvailabilitySlotStatus.BOOKED:
            raise SlotUnavailableError("Slot is already booked.")

        # Check if currently held
        if slot.is_actively_held:
            if slot.held_by_id == user.id:
                # Idempotent hold refresh by the same patient
                slot.hold_expires_at = timezone.now() + timedelta(minutes=5)
                slot.save(update_fields=["hold_expires_at"])
                release_expired_slot_hold.apply_async(args=[str(slot.id)], countdown=300)
                return slot
            else:
                raise SlotUnavailableError("Slot is currently being booked by another patient.")

        # Slot is available or previous hold expired
        slot.status = AvailabilitySlotStatus.HOLD
        slot.held_by = user
        slot.hold_expires_at = timezone.now() + timedelta(minutes=5)
        slot.save(update_fields=["status", "held_by", "hold_expires_at"])

        # Enqueue 5-minute auto-release countdown task to Celery
        release_expired_slot_hold.apply_async(args=[str(slot.id)], countdown=300)
        return slot

    @staticmethod
    @transaction.atomic
    def finalize_booking(
        slot_id: str,
        patient,
        amount: Decimal,
        idempotency_key: str,
        payment_status: str = "success",
        transaction_reference_number: str = None,
    ):
        """
        Verifies ownership and unexpired hold, transitions status: 'hold' -> 'booked',
        creates Consultation and records the Payment ledger entry.
        """
        # Application-level idempotency fallback (Defense in depth):
        # If this exact idempotency key was already recorded, return the existing records immediately
        if idempotency_key:
            existing_payment = (
                Payment.objects.filter(idempotency_key=idempotency_key)
                .select_related("consultation", "consultation__slot", "consultation__doctor")
                .first()
            )
            if existing_payment:
                if (
                    str(existing_payment.consultation.slot_id) != str(slot_id)
                    or existing_payment.amount != Decimal(str(amount))
                    or existing_payment.consultation.patient_id != patient.id
                ):
                    raise IdempotencyConflictError(
                        "Idempotency key reused with mismatched booking parameters."
                    )
                return existing_payment.consultation, existing_payment

        try:
            slot = (
                AvailabilitySlot.objects.select_for_update(nowait=False)
                .select_related("doctor__user")
                .get(id=slot_id)
            )
        except AvailabilitySlot.DoesNotExist:
            raise ValidationError("Availability slot not found.")

        # Domain Guard: Prevent doctor from booking their own slot
        if slot.doctor.user == patient:
            raise ValidationError("Doctors cannot book consultations with themselves.")

        if slot.status == AvailabilitySlotStatus.BOOKED:
            raise ValidationError("Slot has already been booked.")

        if slot.status not in (AvailabilitySlotStatus.HOLD, AvailabilitySlotStatus.LOCKED):
            raise ValidationError(f"Slot must be in 'hold' status prior to confirmation (current status: {slot.status}).")

        # Verify hold expiry
        if slot.hold_expires_at and timezone.now() > slot.hold_expires_at:
            raise ValidationError("Hold on this slot has expired. Please re-hold the slot.")

        # Verify ownership
        if slot.held_by and slot.held_by_id != patient.id:
            raise ValidationError("This slot is held by another patient.")

        consultation = Consultation.objects.create(
            patient=patient,
            doctor=slot.doctor,
            slot=slot,
            status=ConsultationStatus.SCHEDULED,
            meeting_link=f"https://meet.amrutam.co.in/room-{slot.id}",
        )

        payment = Payment.objects.create(
            consultation=consultation,
            amount=amount,
            status=payment_status,
            idempotency_key=idempotency_key,
            transaction_reference_number=transaction_reference_number,
        )

        slot.status = AvailabilitySlotStatus.BOOKED
        slot.held_by = None
        slot.hold_expires_at = None
        slot.save(update_fields=["status", "held_by", "hold_expires_at"])

        return consultation, payment

    @staticmethod
    @transaction.atomic
    def expire_slot(slot_id: str) -> bool:
        """
        Reverts an unfinalized slot back to 'available' if hold has expired.
        """
        try:
            slot = AvailabilitySlot.objects.select_for_update(nowait=False).get(id=slot_id)
            if slot.status in (AvailabilitySlotStatus.HOLD, AvailabilitySlotStatus.LOCKED):
                if not slot.hold_expires_at or timezone.now() >= slot.hold_expires_at:
                    slot.status = AvailabilitySlotStatus.AVAILABLE
                    slot.held_by = None
                    slot.hold_expires_at = None
                    slot.save(update_fields=["status", "held_by", "hold_expires_at"])
                    return True
        except AvailabilitySlot.DoesNotExist:
            pass
        return False

    @classmethod
    def lock_slot(cls, slot_id: uuid.UUID) -> AvailabilitySlot:
        """
        Helper: locks an available slot.
        """
        updated = AvailabilitySlot.objects.filter(
            id=slot_id,
            status=AvailabilitySlotStatus.AVAILABLE,
        ).update(
            status=AvailabilitySlotStatus.HOLD,
            hold_expires_at=timezone.now() + timedelta(minutes=5),
        )

        if updated == 0:
            slot = AvailabilitySlot.objects.filter(id=slot_id).first()
            if not slot:
                raise ValidationError("Slot does not exist.")
            if slot.status != AvailabilitySlotStatus.AVAILABLE:
                raise SlotUnavailableError(f"Slot is no longer available (current status: {slot.status}).")

        return AvailabilitySlot.objects.get(id=slot_id)

    @classmethod
    @transaction.atomic
    def book_slot_and_create_consultation(
        cls,
        patient,
        slot_id: uuid.UUID,
        meeting_link: str = None,
    ) -> Consultation:
        """
        Atomic direct booking helper.
        """
        updated = AvailabilitySlot.objects.filter(
            id=slot_id,
            status__in=[AvailabilitySlotStatus.AVAILABLE, AvailabilitySlotStatus.HOLD, AvailabilitySlotStatus.LOCKED],
        ).update(
            status=AvailabilitySlotStatus.BOOKED,
            held_by=None,
            hold_expires_at=None,
        )

        if updated == 0:
            raise ConcurrencyLockError("Failed to book slot due to concurrent modification or unavailable status.")

        slot = AvailabilitySlot.objects.select_related("doctor").get(id=slot_id)

        consultation = Consultation.objects.create(
            patient=patient,
            doctor=slot.doctor,
            slot=slot,
            status=ConsultationStatus.SCHEDULED,
            meeting_link=meeting_link or "",
        )
        return consultation


class PaymentService:
    """
    Handles payments with strict idempotency guarantees.
    """

    @classmethod
    @transaction.atomic
    def process_payment(
        cls,
        consultation_id: uuid.UUID,
        amount: Decimal,
        idempotency_key: str,
    ) -> tuple[Payment, bool]:
        """
        Processes or retrieves an idempotent payment.
        Returns (payment, created).
        """
        existing_payment = Payment.objects.filter(idempotency_key=idempotency_key).first()
        if existing_payment:
            if (
                str(existing_payment.consultation_id) != str(consultation_id)
                or existing_payment.amount != Decimal(str(amount))
            ):
                raise IdempotencyConflictError(
                    "Idempotency key reused with mismatched consultation or amount parameters."
                )
            return existing_payment, False

        payment = Payment.objects.create(
            consultation_id=consultation_id,
            amount=amount,
            status=PaymentStatus.SUCCESS,
            idempotency_key=idempotency_key,
        )
        return payment, True
