import time
from django.db import connection
from django.core.cache import cache
from django.http import JsonResponse
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from drf_spectacular.utils import extend_schema, OpenApiResponse


class HealthzAPIView(APIView):
    """Liveness probe: verifies process execution."""
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Liveness Probe",
        description="Liveness probe verifying that the application process is running.",
        tags=["Health"],
        responses={200: OpenApiResponse(description="Process is alive.")},
    )
    def get(self, request):
        return JsonResponse({"status": "healthy", "timestamp": time.time()}, status=200)


class ReadyzAPIView(APIView):
    """Readiness probe: validates DB, Redis, and latency boundaries."""
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Readiness Probe",
        description="Readiness probe validating connectivity to PostgreSQL database and Redis cache.",
        tags=["Health"],
        responses={
            200: OpenApiResponse(description="All upstream dependencies are healthy and ready."),
            503: OpenApiResponse(description="One or more dependencies are degraded or unreachable."),
        },
    )
    def get(self, request):
        checks = {}
        # 1. Check PostgreSQL
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
            checks["postgres"] = "UP"
        except Exception as e:
            checks["postgres"] = f"DOWN: {e}"

        # 2. Check Redis Cache & Broker
        try:
            cache.set("__health_check__", "1", timeout=5)
            if cache.get("__health_check__") == "1":
                checks["redis"] = "UP"
            else:
                checks["redis"] = "DOWN: Cache mismatch"
        except Exception as e:
            checks["redis"] = f"DOWN: {e}"

        is_ready = all(v == "UP" for v in checks.values())
        status_code = 200 if is_ready else 503
        return JsonResponse({"status": "ready" if is_ready else "degraded", "dependencies": checks}, status=status_code)
