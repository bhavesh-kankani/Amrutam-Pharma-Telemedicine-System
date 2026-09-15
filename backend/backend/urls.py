from django.conf import settings
from django.contrib import admin
from django.urls import path, re_path, include
from django.views.static import serve
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)
from consultations.views import (
    DoctorListView,
    AvailabilitySlotListView,
    HoldSlotAPIView,
    ConfirmBookingAPIView,
    ConsultationDetailView,
    PrescriptionCreateView,
    PaymentWebhookAPIView,
)
from accounts.mfa_views import MFASetupAPIView, MFAVerifyAPIView
from backend.health import HealthzAPIView, ReadyzAPIView

urlpatterns = [
    # Observability & Metrics
    path("", include("django_prometheus.urls")),
    path("healthz/", HealthzAPIView.as_view(), name="health-liveness"),
    path("readyz/", ReadyzAPIView.as_view(), name="health-readiness"),
    path("admin/", admin.site.urls),
    # OpenAPI 3.0 Documentation Endpoints
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    # JWT Authentication Endpoints
    path("api/v1/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/v1/token/verify/", TokenVerifyView.as_view(), name="token_verify"),
    # Two-Factor Authentication (MFA / RFC 6238 TOTP)
    path("api/v1/auth/mfa/setup/", MFASetupAPIView.as_view(), name="mfa-setup"),
    path("api/v1/auth/mfa/verify/", MFAVerifyAPIView.as_view(), name="mfa-verify"),
    # Telemedicine Core API v1
    path("api/v1/doctors/", DoctorListView.as_view(), name="doctor-list"),
    path("api/v1/slots/", AvailabilitySlotListView.as_view(), name="slot-list"),
    path("api/v1/bookings/hold/", HoldSlotAPIView.as_view(), name="booking-hold"),
    path("api/v1/bookings/confirm/", ConfirmBookingAPIView.as_view(), name="booking-confirm"),
    path("api/v1/consultations/<uuid:pk>/", ConsultationDetailView.as_view(), name="consultation-detail"),
    path("api/v1/prescriptions/", PrescriptionCreateView.as_view(), name="prescription-create"),
    path("api/v1/payments/webhook/", PaymentWebhookAPIView.as_view(), name="payment-webhook"),
    # Admin Analytics API v1
    path("api/v1/admin/", include("analytics.urls")),
]

urlpatterns += [
    re_path(r"^static/(?P<path>.*)$", serve, {"document_root": settings.STATIC_ROOT}),
]
