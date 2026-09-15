from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    AvailabilitySlotViewSet,
    ConsultationViewSet,
    PrescriptionViewSet,
    PaymentViewSet,
)

router = DefaultRouter()
router.register(r"slots", AvailabilitySlotViewSet, basename="availability-slot")
router.register(r"consultations", ConsultationViewSet, basename="consultation")
router.register(r"prescriptions", PrescriptionViewSet, basename="prescription")
router.register(r"payments", PaymentViewSet, basename="payment")

urlpatterns = [
    path("", include(router.urls)),
]
