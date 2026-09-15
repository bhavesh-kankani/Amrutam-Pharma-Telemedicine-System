from decimal import Decimal
from django.db import models
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema

from accounts.models import Doctor
from consultations.models import (
    Consultation,
    Payment,
    PaymentStatus,
    AvailabilitySlot,
    AvailabilitySlotStatus,
)
from .permissions import IsAdminRole
from .serializers import AdminAnalyticsResponseSerializer


class AdminAnalyticsAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    @extend_schema(
        tags=["Admin Analytics"],
        summary="Retrieve Aggregated Admin Analytics",
        description=(
            "Returns aggregated platform metrics including daily consultation volume with 100k target progress, "
            "all-time consultation count, Gross Transaction Value (GTV), doctor-to-slot ratios, slot status breakdowns, "
            "and p95 SLA diagnostic targets."
        ),
        responses={200: AdminAnalyticsResponseSerializer},
    )
    def get(self, request):
        today = timezone.now().date()

        # 1. Daily Consultation Volume
        today_consultations_count = Consultation.objects.filter(created_at__date=today).count()
        target_volume = 100_000
        progress_percentage = round((today_consultations_count / target_volume) * 100, 4)

        # 2. Total Consultations (all-time)
        total_consultations = Consultation.objects.count()

        # 3. Gross Transaction Value (sum of successful payments)
        gtv_agg = Payment.objects.filter(status=PaymentStatus.SUCCESS).aggregate(
            total_sum=models.Sum("amount")
        )
        gross_transaction_value = gtv_agg["total_sum"] if gtv_agg["total_sum"] is not None else Decimal("0.00")

        # 4. Doctor to Slot Ratio
        verified_doctors_count = Doctor.objects.filter(is_verified=True).count()
        now = timezone.now()
        active_slots_today = (
            AvailabilitySlot.objects.filter(start_time__date=today)
            .filter(
                models.Q(status=AvailabilitySlotStatus.AVAILABLE)
                | (
                    models.Q(
                        status__in=[
                            AvailabilitySlotStatus.HOLD,
                            AvailabilitySlotStatus.LOCKED,
                        ]
                    )
                    & models.Q(hold_expires_at__lte=now)
                )
            )
            .count()
        )
        if active_slots_today > 0:
            ratio = round(verified_doctors_count / active_slots_today, 2)
        else:
            ratio = float(verified_doctors_count) if verified_doctors_count else 0.0

        # 5. Slot Breakdown
        available_count = AvailabilitySlot.objects.filter(status=AvailabilitySlotStatus.AVAILABLE).count()
        locked_count = AvailabilitySlot.objects.filter(
            status__in=[AvailabilitySlotStatus.HOLD, AvailabilitySlotStatus.LOCKED]
        ).count()
        booked_count = AvailabilitySlot.objects.filter(status=AvailabilitySlotStatus.BOOKED).count()

        # 6. P95 Latency Summary
        p95_latency_summary = {
            "p95_read_target_ms": 200,
            "p95_write_target_ms": 500,
            "daily_scale_capacity": 100000,
        }

        payload = {
            "daily_consultation_volume": {
                "count": today_consultations_count,
                "target": target_volume,
                "progress_percentage": progress_percentage,
            },
            "total_consultations": total_consultations,
            "gross_transaction_value": gross_transaction_value,
            "doctor_to_slot_ratio": {
                "verified_doctors": verified_doctors_count,
                "active_slots_today": active_slots_today,
                "ratio": ratio,
            },
            "slot_breakdown": {
                "available": available_count,
                "locked": locked_count,
                "booked": booked_count,
            },
            "p95_latency_summary": p95_latency_summary,
        }

        return Response(payload, status=status.HTTP_200_OK)
