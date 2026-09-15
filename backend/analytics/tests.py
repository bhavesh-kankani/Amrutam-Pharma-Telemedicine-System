from decimal import Decimal
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status

from accounts.models import Doctor, Role
from consultations.models import (
    AvailabilitySlot,
    AvailabilitySlotStatus,
    Consultation,
    ConsultationStatus,
    Payment,
    PaymentStatus,
)

User = get_user_model()


class AdminAnalyticsAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Admin user
        self.admin_user = User.objects.create_superuser(
            email="admin_analytics@amrutam.com",
            password="AdminPassword123!",
            role=Role.ADMIN,
        )

        # Patient user
        self.patient = User.objects.create_user(
            email="patient_analytics@amrutam.com",
            password="Password123!",
            role=Role.PATIENT,
        )

        # Doctor user & doctor record
        self.doctor_user = User.objects.create_user(
            email="doctor_analytics@amrutam.com",
            password="Password123!",
            role=Role.DOCTOR,
        )
        self.doctor = Doctor.objects.create(
            user=self.doctor_user,
            specialization="Ayurvedic Rasayana",
            license_number="ANALYTICS-DOC-001",
            consultation_fee=Decimal("600.00"),
            is_verified=True,
        )

        now = timezone.now()
        # Today's slots
        self.slot_available = AvailabilitySlot.objects.create(
            doctor=self.doctor,
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=2),
            status=AvailabilitySlotStatus.AVAILABLE,
        )
        self.slot_locked = AvailabilitySlot.objects.create(
            doctor=self.doctor,
            start_time=now + timedelta(hours=3),
            end_time=now + timedelta(hours=4),
            status=AvailabilitySlotStatus.HOLD,
            hold_expires_at=now + timedelta(minutes=5),
            held_by=self.patient,
        )
        self.slot_booked = AvailabilitySlot.objects.create(
            doctor=self.doctor,
            start_time=now + timedelta(hours=5),
            end_time=now + timedelta(hours=6),
            status=AvailabilitySlotStatus.BOOKED,
        )

        # Consultations
        self.consultation_today = Consultation.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            slot=self.slot_booked,
            status=ConsultationStatus.SCHEDULED,
        )

        # Payments
        Payment.objects.create(
            consultation=self.consultation_today,
            amount=Decimal("600.00"),
            status=PaymentStatus.SUCCESS,
            idempotency_key="analytics_pmt_key_1",
        )
        Payment.objects.create(
            consultation=self.consultation_today,
            amount=Decimal("100.00"),
            status=PaymentStatus.FAILED,
            idempotency_key="analytics_pmt_key_2",
        )

    def test_analytics_permission_anonymous(self):
        response = self.client.get("/api/v1/admin/analytics/")
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_analytics_permission_patient_forbidden(self):
        self.client.force_authenticate(user=self.patient)
        response = self.client.get("/api/v1/admin/analytics/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_analytics_permission_doctor_forbidden(self):
        self.client.force_authenticate(user=self.doctor_user)
        response = self.client.get("/api/v1/admin/analytics/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_analytics_success_for_admin(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get("/api/v1/admin/analytics/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = response.data
        # 1. Daily Consultation Volume
        self.assertIn("daily_consultation_volume", data)
        self.assertEqual(data["daily_consultation_volume"]["count"], 1)
        self.assertEqual(data["daily_consultation_volume"]["target"], 100000)
        self.assertEqual(data["daily_consultation_volume"]["progress_percentage"], 0.001)

        # 2. Total Consultations
        self.assertEqual(data["total_consultations"], 1)

        # 3. Gross Transaction Value (only success: 600.00)
        self.assertEqual(Decimal(str(data["gross_transaction_value"])), Decimal("600.00"))

        # 4. Doctor to Slot Ratio
        self.assertIn("doctor_to_slot_ratio", data)
        self.assertEqual(data["doctor_to_slot_ratio"]["verified_doctors"], 1)
        # Active slots today: slot_available (status=available) = 1
        self.assertEqual(data["doctor_to_slot_ratio"]["active_slots_today"], 1)
        self.assertEqual(data["doctor_to_slot_ratio"]["ratio"], 1.0)

        # 5. Slot Breakdown
        self.assertIn("slot_breakdown", data)
        self.assertEqual(data["slot_breakdown"]["available"], 1)
        self.assertEqual(data["slot_breakdown"]["locked"], 1)
        self.assertEqual(data["slot_breakdown"]["booked"], 1)

        # 6. P95 Latency Summary
        self.assertIn("p95_latency_summary", data)
        self.assertEqual(data["p95_latency_summary"]["p95_read_target_ms"], 200)
        self.assertEqual(data["p95_latency_summary"]["p95_write_target_ms"], 500)
        self.assertEqual(data["p95_latency_summary"]["daily_scale_capacity"], 100000)


class HealthCheckAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_healthz_liveness_probe(self):
        response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("timestamp", data)

    def test_readyz_readiness_probe(self):
        response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["status"], "ready")
        self.assertEqual(data["dependencies"]["postgres"], "UP")
        self.assertEqual(data["dependencies"]["redis"], "UP")

