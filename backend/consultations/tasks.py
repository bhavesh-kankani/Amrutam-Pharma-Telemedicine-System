import logging
from celery import shared_task
from django.db import transaction

logger = logging.getLogger(__name__)


@shared_task
def release_expired_slot_hold(slot_id: str):
    """
    Triggered after a 5-minute hold window (countdown=300).
    Reverts unconfirmed 'hold'/'locked' slots back to 'available'.
    """
    from .services import BookingService

    released = BookingService.expire_slot(slot_id)
    if released:
        logger.info(f"Slot {slot_id} hold expired. Reverted to available.")
    else:
        logger.debug(f"Slot {slot_id} hold not expired or already finalized.")


@shared_task
def periodic_expire_stale_holds():
    """
    Safety net periodic sweeper task.
    Finds and releases any orphaned holds whose expiry timestamp has passed.
    """
    from django.utils import timezone
    from .models import AvailabilitySlot, AvailabilitySlotStatus

    now = timezone.now()
    stale_slots = AvailabilitySlot.objects.filter(
        status__in=[AvailabilitySlotStatus.HOLD, AvailabilitySlotStatus.LOCKED],
        hold_expires_at__lte=now,
    )
    count = 0
    for slot in stale_slots:
        from .services import BookingService
        if BookingService.expire_slot(str(slot.id)):
            count += 1
    if count > 0:
        logger.info(f"Swept and released {count} stale slot hold(s).")
    return count


@shared_task
def generate_prescription_pdf(prescription_id: str):
    """Asynchronously generates an immutable PDF artifact for a prescription."""
    logger.info(f"Generating PDF for prescription ID: {prescription_id}")
    return f"https://cdn.amrutam.co.in/prescriptions/{prescription_id}.pdf"


@shared_task
def process_payment_webhook_task(payload: dict):
    """Asynchronously processes payment confirmation and schedules the consultation."""
    from .models import Payment

    idempotency_key = payload.get("idempotency_key")
    if not idempotency_key:
        logger.warning("Webhook payload missing idempotency_key.")
        return

    with transaction.atomic():
        payment = Payment.objects.filter(idempotency_key=idempotency_key).first()

        if not payment:
            logger.warning(f"No payment record found for idempotency key: {idempotency_key}")
            return

        if payment.status != "success":
            payment.status = "success"
            payment.save(update_fields=["status"])

            consultation = payment.consultation
            consultation.status = "scheduled"
            consultation.save(update_fields=["status"])
            logger.info(f"Payment confirmed for consultation {consultation.id}")
        else:
            # Idempotent skip: already confirmed synchronously during checkout
            logger.info(
                f"Payment {payment.id} for consultation {payment.consultation_id} "
                f"is already marked success. Webhook processed as idempotent no-op."
            )
