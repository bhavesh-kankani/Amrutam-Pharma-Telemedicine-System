from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.exceptions import ValidationError
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, OpenApiResponse
from .permissions import CanBookConsultation

from accounts.models import Doctor
from .models import AvailabilitySlot, Consultation, Prescription
from .filters import DoctorFilter
from .serializers import (
    DoctorSerializer,
    AvailabilitySlotSerializer,
    HoldSlotSerializer,
    ConfirmBookingSerializer,
    PaymentWebhookSerializer,
    PrescriptionSerializer,
    ConsultationDetailSerializer,
)
from .services import BookingService, IdempotencyConflictError
from .tasks import process_payment_webhook_task, generate_prescription_pdf


@extend_schema(
    tags=["Doctors"],
    summary="List & Filter Verified Doctors",
    description="Search and filter verified doctors by specialization (case-insensitive substring), fee range (min_fee, max_fee), and availability_date.",
)
class DoctorListView(generics.ListAPIView):
    permission_classes = [AllowAny]
    queryset = Doctor.objects.filter(is_verified=True).select_related("user")
    serializer_class = DoctorSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = DoctorFilter


@extend_schema(
    tags=["Availability Slots"],
    summary="List Availability Slots",
    description="List doctor availability slots with real-time dynamic display status (AVAILABLE, UNDER_BOOKING, BOOKED) and remaining seconds.",
)
class AvailabilitySlotListView(generics.ListAPIView):
    permission_classes = [AllowAny]
    queryset = AvailabilitySlot.objects.select_related("doctor__user").all().order_by("start_time")
    serializer_class = AvailabilitySlotSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["doctor", "status"]


class HoldSlotAPIView(APIView):
    permission_classes = [IsAuthenticated, CanBookConsultation]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "booking_hold"

    @extend_schema(
        tags=["Bookings"],
        summary="Place Explicit 5-Minute Hold on Slot",
        description="Atomically holds an available or expired slot for 5 minutes, bound to the authenticated patient.",
        request=HoldSlotSerializer,
        responses={
            200: OpenApiResponse(description="Slot held successfully for 5 minutes."),
            400: OpenApiResponse(description="Validation error (e.g. doctor booking self)."),
            409: OpenApiResponse(description="Conflict: Slot is actively being booked by another patient."),
        },
    )
    def post(self, request):
        serializer = HoldSlotSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            slot = BookingService.hold_slot(
                slot_id=str(serializer.validated_data["slot_id"]),
                caller_user=request.user,
            )
            return Response(
                {
                    "message": "Slot held for 5 minutes",
                    "slot_id": slot.id,
                    "status": slot.status,
                    "display_status": slot.display_status,
                    "remaining_seconds": slot.remaining_seconds,
                },
                status=status.HTTP_200_OK,
            )
        except ValidationError as e:
            return Response({"error": e.detail if hasattr(e, "detail") else str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_409_CONFLICT)


class ConfirmBookingAPIView(APIView):
    permission_classes = [IsAuthenticated, CanBookConsultation]

    @extend_schema(
        tags=["Bookings"],
        summary="Confirm Booking & Record Payment",
        description="Validates that the slot is actively held by caller, creates Consultation and records Payment ledger entry with optional transaction reference.",
        request=ConfirmBookingSerializer,
        responses={
            201: OpenApiResponse(description="Booking confirmed and Consultation created."),
            400: OpenApiResponse(description="Invalid request or expired hold window."),
        },
    )
    def post(self, request):
        serializer = ConfirmBookingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            consultation, payment = BookingService.finalize_booking(
                slot_id=str(data["slot_id"]),
                patient=request.user,
                amount=data["amount"],
                idempotency_key=data["idempotency_key"],
                payment_status=data.get("payment_status", "success"),
                transaction_reference_number=data.get("transaction_reference_number"),
            )
            return Response(
                {
                    "consultation_id": consultation.id,
                    "meeting_link": consultation.meeting_link,
                    "payment_status": payment.status,
                    "transaction_reference_number": payment.transaction_reference_number,
                    "slot_status": consultation.slot.status,
                },
                status=status.HTTP_201_CREATED,
            )
        except IdempotencyConflictError as e:
            return Response({"error": str(e)}, status=status.HTTP_409_CONFLICT)
        except ValidationError as e:
            return Response({"error": e.detail if hasattr(e, "detail") else str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class PaymentWebhookAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "webhook"

    @extend_schema(
        tags=["Payments"],
        summary="Receive Payment Webhook Event",
        description="Receives payment gateway notifications and enqueues Celery background task for asynchronous verification and consultation scheduling.",
        request=PaymentWebhookSerializer,
        responses={200: OpenApiResponse(description="Webhook acknowledged.")},
    )
    def post(self, request):
        serializer = PaymentWebhookSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        process_payment_webhook_task.delay(serializer.validated_data)
        return Response({"status": "acknowledged"}, status=status.HTTP_200_OK)


@extend_schema(
    tags=["Prescriptions"],
    summary="Create Digital Medical Prescription",
    description="Creates an immutable digital prescription and schedules Celery task for PDF generation.",
)
class PrescriptionCreateView(generics.CreateAPIView):
    queryset = Prescription.objects.all()
    serializer_class = PrescriptionSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        instance = serializer.save()
        generate_prescription_pdf.delay(str(instance.id))


@extend_schema(
    tags=["Consultations"],
    summary="Retrieve Consultation Details",
    description="Retrieves comprehensive details of a consultation, including doctor details, slot info, and issued prescriptions.",
)
class ConsultationDetailView(generics.RetrieveAPIView):
    queryset = Consultation.objects.select_related("patient", "doctor__user", "slot").prefetch_related("prescriptions").all()
    serializer_class = ConsultationDetailSerializer
    permission_classes = [IsAuthenticated]
