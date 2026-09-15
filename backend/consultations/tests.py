import uuid
from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from django.db import IntegrityError
from rest_framework.test import APIClient
from rest_framework import status

from accounts.models import Doctor, Role
from consultations.models import (
    AvailabilitySlot,
    AvailabilitySlotStatus,
    Consultation,
    ConsultationStatus,
    Prescription,
    Payment,
    PaymentStatus,
)
from consultations.services import (
    BookingService,
    PaymentService,
    SlotUnavailableError,
    IdempotencyConflictError,
)
from consultations.tasks import (
    release_expired_slot_hold,
    periodic_expire_stale_holds,
    generate_prescription_pdf,
    process_payment_webhook_task,
)

User = get_user_model()


class ConsultationsServiceAndModelTests(TestCase):
    def setUp(self):
        self.patient = User.objects.create_user(
            email="patient_consult@example.com",
            password="Password123!",
            role=Role.PATIENT,
        )
        self.other_patient = User.objects.create_user(
            email="patient_other@example.com",
            password="Password123!",
            role=Role.PATIENT,
        )
        self.doctor_user = User.objects.create_user(
            email="doctor_consult@example.com",
            password="Password123!",
            role=Role.DOCTOR,
        )
        self.doctor = Doctor.objects.create(
            user=self.doctor_user,
            specialization="Panchakarma",
            license_number="PK-2026-001",
            consultation_fee=Decimal("200.00"),
            is_verified=True,
        )
        now = timezone.now()
        self.slot = AvailabilitySlot.objects.create(
            doctor=self.doctor,
            start_time=now + timedelta(days=1),
            end_time=now + timedelta(days=1, hours=1),
            status=AvailabilitySlotStatus.AVAILABLE,
        )

    @patch("consultations.tasks.release_expired_slot_hold.apply_async")
    def test_explicit_temporary_hold_pattern(self, mock_apply_async):
        # 1. Patient 1 holds the slot
        slot = BookingService.hold_slot(str(self.slot.id), self.patient)
        self.assertEqual(slot.status, AvailabilitySlotStatus.HOLD)
        self.assertEqual(slot.held_by, self.patient)
        self.assertIsNotNone(slot.hold_expires_at)
        self.assertTrue(slot.is_actively_held)
        self.assertEqual(slot.display_status, "UNDER_BOOKING")
        self.assertGreater(slot.remaining_seconds, 0)
        mock_apply_async.assert_called_once_with(args=[str(self.slot.id)], countdown=300)

        # 2. Patient 2 attempts to hold the same slot -> SlotUnavailableError
        with self.assertRaises(SlotUnavailableError):
            BookingService.hold_slot(str(self.slot.id), self.other_patient)

    def test_self_healing_read_queries(self):
        # Slot in HOLD with expired hold_expires_at
        now = timezone.now()
        self.slot.status = AvailabilitySlotStatus.HOLD
        self.slot.held_by = self.patient
        self.slot.hold_expires_at = now - timedelta(seconds=10)
        self.slot.save()

        self.assertFalse(self.slot.is_actively_held)
        # Self-healing: displays as AVAILABLE without requiring DB write
        self.assertEqual(self.slot.display_status, "AVAILABLE")
        self.assertIsNone(self.slot.remaining_seconds)

        # Now a new patient can hold it
        slot = BookingService.hold_slot(str(self.slot.id), self.other_patient)
        self.assertEqual(slot.held_by, self.other_patient)
        self.assertTrue(slot.is_actively_held)

    @patch("consultations.tasks.release_expired_slot_hold.apply_async")
    def test_finalize_booking_with_ownership_and_transaction_ref(self, mock_apply_async):
        BookingService.hold_slot(str(self.slot.id), self.patient)

        # Patient 2 attempting to finalize Patient 1's hold must fail
        with self.assertRaises((ValidationError, DRFValidationError)) as ctx:
            BookingService.finalize_booking(
                slot_id=str(self.slot.id),
                patient=self.other_patient,
                amount=Decimal("200.00"),
                idempotency_key="finalize_key_wrong_user",
            )
        self.assertIn("held by another patient", str(ctx.exception))

        # Patient 1 finalizes booking with transaction_reference_number
        consultation, payment = BookingService.finalize_booking(
            slot_id=str(self.slot.id),
            patient=self.patient,
            amount=Decimal("200.00"),
            idempotency_key="finalize_key_patient1",
            payment_status="success",
            transaction_reference_number="UPI-REF-1234567890",
        )
        self.assertEqual(consultation.status, ConsultationStatus.SCHEDULED)
        self.assertEqual(consultation.doctor, self.doctor)
        self.assertEqual(payment.status, PaymentStatus.SUCCESS)
        self.assertEqual(payment.amount, Decimal("200.00"))
        self.assertEqual(payment.transaction_reference_number, "UPI-REF-1234567890")

        self.slot.refresh_from_db()
        self.assertEqual(self.slot.status, AvailabilitySlotStatus.BOOKED)
        self.assertIsNone(self.slot.held_by)
        self.assertIsNone(self.slot.hold_expires_at)
        self.assertEqual(self.slot.display_status, "BOOKED")

    def test_finalize_booking_fails_when_hold_expired(self):
        now = timezone.now()
        self.slot.status = AvailabilitySlotStatus.HOLD
        self.slot.held_by = self.patient
        self.slot.hold_expires_at = now - timedelta(minutes=1)
        self.slot.save()

        with self.assertRaises((ValidationError, DRFValidationError)) as ctx:
            BookingService.finalize_booking(
                slot_id=str(self.slot.id),
                patient=self.patient,
                amount=Decimal("200.00"),
                idempotency_key="expired_hold_key",
            )
        self.assertIn("expired", str(ctx.exception).lower())

        # Verify self-healing dynamic status
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.display_status, "AVAILABLE")

        # Background sweeper cleans DB state
        BookingService.expire_slot(str(self.slot.id))
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.status, AvailabilitySlotStatus.AVAILABLE)
        self.assertIsNone(self.slot.held_by)

    def test_celery_task_release_expired_slot_hold(self):
        now = timezone.now()
        self.slot.status = AvailabilitySlotStatus.HOLD
        self.slot.held_by = self.patient
        self.slot.hold_expires_at = now - timedelta(seconds=1)
        self.slot.save()

        release_expired_slot_hold(str(self.slot.id))
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.status, AvailabilitySlotStatus.AVAILABLE)
        self.assertIsNone(self.slot.held_by)
        self.assertIsNone(self.slot.hold_expires_at)

    def test_periodic_expire_stale_holds(self):
        now = timezone.now()
        self.slot.status = AvailabilitySlotStatus.HOLD
        self.slot.held_by = self.patient
        self.slot.hold_expires_at = now - timedelta(minutes=2)
        self.slot.save()

        swept_count = periodic_expire_stale_holds()
        self.assertGreaterEqual(swept_count, 1)

        self.slot.refresh_from_db()
        self.assertEqual(self.slot.status, AvailabilitySlotStatus.AVAILABLE)

    def test_payment_transaction_reference_number_uniqueness(self):
        consultation = BookingService.book_slot_and_create_consultation(
            patient=self.patient,
            slot_id=self.slot.id,
        )
        Payment.objects.create(
            consultation=consultation,
            amount=Decimal("200.00"),
            status=PaymentStatus.SUCCESS,
            idempotency_key="uniq_test_1",
            transaction_reference_number="BANK-UTR-999888",
        )

        with self.assertRaises(IntegrityError):
            Payment.objects.create(
                consultation=consultation,
                amount=Decimal("200.00"),
                status=PaymentStatus.SUCCESS,
                idempotency_key="uniq_test_2",
                transaction_reference_number="BANK-UTR-999888",
            )

    def test_prescription_immutability(self):
        consultation = BookingService.book_slot_and_create_consultation(
            patient=self.patient,
            slot_id=self.slot.id,
        )
        prescription = Prescription.objects.create(
            consultation=consultation,
            medications=[
                {"drug": "Ashwagandha", "dosage": "500mg", "duration": "30 days"}
            ],
            notes="Take daily with warm milk",
        )
        self.assertIsNotNone(prescription.issued_at)

        # Attempting to modify prescription must raise ValidationError
        prescription.notes = "Altered prescription note"
        with self.assertRaises(ValidationError):
            prescription.save()

    def test_payment_idempotency(self):
        consultation = BookingService.book_slot_and_create_consultation(
            patient=self.patient,
            slot_id=self.slot.id,
        )
        idempotency_key = "idemp_test_key_12345678"

        # First payment attempt
        payment1, created1 = PaymentService.process_payment(
            consultation_id=consultation.id,
            amount=Decimal("200.00"),
            idempotency_key=idempotency_key,
        )
        self.assertTrue(created1)
        self.assertEqual(payment1.status, PaymentStatus.SUCCESS)

        # Duplicate payment attempt with identical key and parameters
        payment2, created2 = PaymentService.process_payment(
            consultation_id=consultation.id,
            amount=Decimal("200.00"),
            idempotency_key=idempotency_key,
        )
        self.assertFalse(created2)
        self.assertEqual(payment1.id, payment2.id)
        self.assertEqual(Payment.objects.filter(idempotency_key=idempotency_key).count(), 1)

        # Attempt to reuse idempotency key with mismatched amount
        with self.assertRaises(IdempotencyConflictError):
            PaymentService.process_payment(
                consultation_id=consultation.id,
                amount=Decimal("999.00"),
                idempotency_key=idempotency_key,
            )


class ConsultationPhase2And3APITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.patient = User.objects.create_user(
            email="patient_api@amrutam.com",
            password="Password123!",
            role=Role.PATIENT,
        )
        self.patient2 = User.objects.create_user(
            email="patient2_api@amrutam.com",
            password="Password123!",
            role=Role.PATIENT,
        )
        self.doctor_user = User.objects.create_user(
            email="doctor_api@amrutam.com",
            password="Password123!",
            role=Role.DOCTOR,
        )
        self.doctor = Doctor.objects.create(
            user=self.doctor_user,
            specialization="Ayurvedic Kayachikitsa",
            license_number="DOC-API-001",
            consultation_fee=Decimal("500.00"),
            is_verified=True,
        )
        now = timezone.now()
        self.slot = AvailabilitySlot.objects.create(
            doctor=self.doctor,
            start_time=now + timedelta(days=2),
            end_time=now + timedelta(days=2, hours=1),
            status=AvailabilitySlotStatus.AVAILABLE,
        )

    def test_doctor_list_and_filter(self):
        response = self.client.get("/api/v1/doctors/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["license_number"], "DOC-API-001")

        # 1. Filter by specialization (icontains)
        resp_filtered = self.client.get("/api/v1/doctors/?specialization=kayachikitsa")
        self.assertEqual(resp_filtered.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp_filtered.data), 1)

        resp_empty = self.client.get("/api/v1/doctors/?specialization=NonExistent")
        self.assertEqual(resp_empty.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp_empty.data), 0)

        # 2. Filter by fee range (min_fee and max_fee)
        resp_fee_match = self.client.get("/api/v1/doctors/?min_fee=400&max_fee=600")
        self.assertEqual(resp_fee_match.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp_fee_match.data), 1)

        resp_fee_too_low = self.client.get("/api/v1/doctors/?max_fee=300")
        self.assertEqual(resp_fee_too_low.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp_fee_too_low.data), 0)

        resp_fee_too_high = self.client.get("/api/v1/doctors/?min_fee=800")
        self.assertEqual(resp_fee_too_high.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp_fee_too_high.data), 0)

        # 3. Filter by availability_date
        slot_date = self.slot.start_time.date().isoformat()
        resp_date_match = self.client.get(f"/api/v1/doctors/?availability_date={slot_date}")
        self.assertEqual(resp_date_match.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp_date_match.data), 1)

        other_date = (self.slot.start_time + timedelta(days=10)).date().isoformat()
        resp_date_no_slot = self.client.get(f"/api/v1/doctors/?availability_date={other_date}")
        self.assertEqual(resp_date_no_slot.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp_date_no_slot.data), 0)

    def test_slot_list_and_filter(self):
        response = self.client.get("/api/v1/slots/?status=available")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], str(self.slot.id))
        self.assertEqual(response.data[0]["display_status"], "AVAILABLE")

    @patch("consultations.tasks.release_expired_slot_hold.apply_async")
    def test_hold_and_confirm_booking_flow(self, mock_apply_async):
        self.client.force_authenticate(user=self.patient)

        # 1. Patient 1 holds slot
        hold_resp = self.client.post(
            "/api/v1/bookings/hold/",
            {"slot_id": str(self.slot.id)},
            format="json",
        )
        self.assertEqual(hold_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(hold_resp.data["status"], "hold")
        self.assertEqual(hold_resp.data["display_status"], "UNDER_BOOKING")

        # Verify slot listing now reflects UNDER_BOOKING for other patients
        slot_list_resp = self.client.get("/api/v1/slots/")
        self.assertEqual(slot_list_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(slot_list_resp.data[0]["display_status"], "UNDER_BOOKING")

        # 2. Patient 2 attempts to hold -> 409 Conflict
        client2 = APIClient()
        client2.force_authenticate(user=self.patient2)
        hold_conflict = client2.post(
            "/api/v1/bookings/hold/",
            {"slot_id": str(self.slot.id)},
            format="json",
        )
        self.assertEqual(hold_conflict.status_code, status.HTTP_409_CONFLICT)

        # 3. Confirm booking by Patient 1 with transaction_reference_number
        idem_key = f"api_test_idemp_{uuid.uuid4().hex}"
        txn_ref = f"TXN-UPI-{uuid.uuid4().hex[:10]}"
        confirm_resp = self.client.post(
            "/api/v1/bookings/confirm/",
            {
                "slot_id": str(self.slot.id),
                "amount": "500.00",
                "idempotency_key": idem_key,
                "payment_status": "success",
                "transaction_reference_number": txn_ref,
            },
            format="json",
        )
        self.assertEqual(confirm_resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(confirm_resp.data["payment_status"], "success")
        self.assertEqual(confirm_resp.data["transaction_reference_number"], txn_ref)
        self.assertEqual(confirm_resp.data["slot_status"], "booked")
        consultation_id = str(confirm_resp.data["consultation_id"])

        # 3b. Idempotent Retry: Immediate repeat of identical confirmation must return 201 Created (not 400)
        confirm_retry = self.client.post(
            "/api/v1/bookings/confirm/",
            {
                "slot_id": str(self.slot.id),
                "amount": "500.00",
                "idempotency_key": idem_key,
                "payment_status": "success",
                "transaction_reference_number": txn_ref,
            },
            format="json",
        )
        self.assertEqual(confirm_retry.status_code, status.HTTP_201_CREATED)
        self.assertEqual(str(confirm_retry.data["consultation_id"]), consultation_id)
        self.assertEqual(confirm_retry.data["payment_status"], "success")

        # 3c. Application-level fallback: Even if Redis cache is evicted, DB idempotency returns 201 Created
        from django.core.cache import cache
        cache.delete(f"idempotency:{idem_key}")
        confirm_retry_db = self.client.post(
            "/api/v1/bookings/confirm/",
            {
                "slot_id": str(self.slot.id),
                "amount": "500.00",
                "idempotency_key": idem_key,
                "payment_status": "success",
                "transaction_reference_number": txn_ref,
            },
            format="json",
        )
        self.assertEqual(confirm_retry_db.status_code, status.HTTP_201_CREATED)
        self.assertEqual(str(confirm_retry_db.data["consultation_id"]), consultation_id)

        # 3d. Conflict on mismatched parameter reuse of idempotency key
        confirm_conflict = self.client.post(
            "/api/v1/bookings/confirm/",
            {
                "slot_id": str(self.slot.id),
                "amount": "999.00",
                "idempotency_key": idem_key,
            },
            format="json",
        )
        self.assertEqual(confirm_conflict.status_code, status.HTTP_409_CONFLICT)

        # 4. Retrieve consultation details
        detail_resp = self.client.get(f"/api/v1/consultations/{consultation_id}/")
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_resp.data["patient"], self.patient.id)

    @patch("consultations.tasks.generate_prescription_pdf.delay")
    def test_prescription_create_view(self, mock_delay):
        self.client.force_authenticate(user=self.patient)
        consultation = Consultation.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            slot=self.slot,
            status=ConsultationStatus.SCHEDULED,
        )

        resp = self.client.post(
            "/api/v1/prescriptions/",
            {
                "consultation": str(consultation.id),
                "medications": [{"drug": "Triphala", "dosage": "250mg", "duration": "14 days"}],
                "notes": "After dinner",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        mock_delay.assert_called_once_with(str(resp.data["id"]))

    @patch("consultations.tasks.process_payment_webhook_task.delay")
    def test_payment_webhook_endpoint(self, mock_delay):
        payload = {"idempotency_key": f"wh_test_key_{uuid.uuid4().hex}", "event": "payment.success"}
        resp = self.client.post("/api/v1/payments/webhook/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, {"status": "acknowledged"})
        mock_delay.assert_called_once_with(payload)

    def test_payment_webhook_task_execution(self):
        consultation = Consultation.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            slot=self.slot,
            status=ConsultationStatus.SCHEDULED,
        )
        payment = Payment.objects.create(
            consultation=consultation,
            amount=Decimal("500.00"),
            status=PaymentStatus.PENDING,
            idempotency_key="wh_task_key_999",
        )

        # First run: Transitions pending -> success
        process_payment_webhook_task({"idempotency_key": "wh_task_key_999"})
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.SUCCESS)

        # Second run: Idempotent no-op execution
        process_payment_webhook_task({"idempotency_key": "wh_task_key_999"})
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.SUCCESS)

    def test_generate_prescription_pdf_task(self):
        url = generate_prescription_pdf("dummy-prescription-uuid")
        self.assertIn("dummy-prescription-uuid.pdf", url)

    def test_idempotency_middleware(self):
        from django.core.cache import cache

        self.client.force_authenticate(user=self.patient)

        idem_key = f"test_middleware_key_{uuid.uuid4().hex}"
        cache.delete(f"idempotency:{idem_key}")

        # First request with X-Idempotency-Key
        resp1 = self.client.post(
            "/api/v1/payments/webhook/",
            {
                "event": "payment.success",
                "idempotency_key": f"middleware_test_{uuid.uuid4().hex}",
            },
            format="json",
            HTTP_X_IDEMPOTENCY_KEY=idem_key,
        )
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        self.assertEqual(resp1.headers.get("X-Cache-Lookup"), "MISS")

        # Second request with identical X-Idempotency-Key
        resp2 = self.client.post(
            "/api/v1/payments/webhook/",
            {
                "event": "payment.success",
                "idempotency_key": "different_ignored_due_to_cache",
            },
            format="json",
            HTTP_X_IDEMPOTENCY_KEY=idem_key,
        )
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertEqual(resp2.headers.get("X-Cache-Lookup"), "HIT")
        self.assertEqual(resp2.json(), resp1.json())

    def test_openapi_documentation_endpoints(self):
        # 1. Schema endpoint
        resp_schema = self.client.get("/api/schema/")
        self.assertEqual(resp_schema.status_code, status.HTTP_200_OK)

        # 2. Swagger UI endpoint
        resp_swagger = self.client.get("/api/docs/")
        self.assertEqual(resp_swagger.status_code, status.HTTP_200_OK)

        # 3. Redoc endpoint
        resp_redoc = self.client.get("/api/redoc/")
        self.assertEqual(resp_redoc.status_code, status.HTTP_200_OK)

    def test_jwt_obtain_and_refresh_token(self):
        # 1. Obtain JWT token pair
        resp = self.client.post(
            "/api/v1/token/",
            {"email": "patient_api@amrutam.com", "password": "Password123!"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)
        refresh_token = resp.data["refresh"]

        # 2. Refresh token
        refresh_resp = self.client.post(
            "/api/v1/token/refresh/",
            {"refresh": refresh_token},
            format="json",
        )
        self.assertEqual(refresh_resp.status_code, status.HTTP_200_OK)
        self.assertIn("access", refresh_resp.data)

    def test_jwt_authenticated_request(self):
        # Obtain token
        token_resp = self.client.post(
            "/api/v1/token/",
            {"email": "patient_api@amrutam.com", "password": "Password123!"},
            format="json",
        )
        access_token = token_resp.data["access"]

        # Make authenticated request with Bearer header
        consultation = Consultation.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            slot=self.slot,
            status=ConsultationStatus.SCHEDULED,
        )
        resp = self.client.get(
            f"/api/v1/consultations/{consultation.id}/",
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["patient"], self.patient.id)

    def test_admin_cannot_book_consultation(self):
        admin_user = User.objects.create_superuser(
            email="admin_booking_test@amrutam.com",
            password="Password123!",
            role=Role.ADMIN,
        )
        self.client.force_authenticate(user=admin_user)
        resp = self.client.post(
            "/api/v1/bookings/hold/",
            {"slot_id": str(self.slot.id)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_doctor_cannot_book_own_slot(self):
        self.client.force_authenticate(user=self.doctor_user)
        resp = self.client.post(
            "/api/v1/bookings/hold/",
            {"slot_id": str(self.slot.id)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        error_msg = str(resp.data.get("error", ""))
        self.assertIn("Doctors cannot book consultations with themselves", error_msg)

    @patch("consultations.tasks.release_expired_slot_hold.apply_async")
    def test_doctor_can_book_another_doctor_slot(self, mock_apply_async):
        doctor2_user = User.objects.create_user(
            email="doctor2_api@amrutam.com",
            password="Password123!",
            role=Role.DOCTOR,
        )
        self.client.force_authenticate(user=doctor2_user)
        resp = self.client.post(
            "/api/v1/bookings/hold/",
            {"slot_id": str(self.slot.id)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["status"], "hold")

