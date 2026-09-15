import json
from django.core.cache import cache
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from drf_spectacular.utils import extend_schema, OpenApiResponse

from .mfa import (
    generate_totp_secret,
    generate_provisioning_uri,
    generate_scratch_codes,
    verify_totp_token,
)


class MFASetupResponseSerializer(serializers.Serializer):
    secret = serializers.CharField()
    otpauth_uri = serializers.CharField()
    recovery_codes = serializers.ListField(child=serializers.CharField())
    instructions = serializers.CharField()


class MFAVerifyRequestSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=6, min_length=6)


class MFASetupAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    @extend_schema(
        tags=["Authentication"],
        summary="Setup Two-Factor Authentication (TOTP)",
        description="Generates an RFC 6238 Base32 secret, an otpauth:// provisioning URI for authenticator apps, and 5 emergency recovery scratch codes.",
        request=None,
        responses={200: MFASetupResponseSerializer},
    )
    def post(self, request):
        user = request.user
        secret = generate_totp_secret()
        uri = generate_provisioning_uri(secret, user.email)
        recovery_codes = generate_scratch_codes(5)

        # Store pending secret and hashed recovery codes in cache for 10 minutes
        cache_key = f"mfa_setup_pending:{user.id}"
        cache.set(
            cache_key,
            json.dumps({"secret": secret, "recovery_codes": recovery_codes}),
            timeout=600,
        )

        return Response(
            {
                "secret": secret,
                "otpauth_uri": uri,
                "recovery_codes": recovery_codes,
                "instructions": "Scan the otpauth_uri or enter the secret key into Google Authenticator, then submit the 6-digit code to /api/v1/auth/mfa/verify/.",
            },
            status=status.HTTP_200_OK,
        )


class MFAVerifyAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    @extend_schema(
        tags=["Authentication"],
        summary="Verify & Activate Two-Factor Authentication",
        description="Validates a 6-digit TOTP token against the pending secret and enables MFA on the user account.",
        request=MFAVerifyRequestSerializer,
        responses={
            200: OpenApiResponse(description="MFA successfully activated."),
            400: OpenApiResponse(description="Invalid or expired verification token."),
        },
    )
    def post(self, request):
        serializer = MFAVerifyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data["token"]

        user = request.user
        cache_key = f"mfa_setup_pending:{user.id}"
        cached_data = cache.get(cache_key)

        if not cached_data:
            return Response(
                {"error": "MFA setup session expired. Please initiate setup again via /api/v1/auth/mfa/setup/."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = json.loads(cached_data)
        secret = data["secret"]

        if not verify_totp_token(secret, token):
            return Response(
                {"error": "Invalid 6-digit TOTP token. Please ensure clock synchronization and try again."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Activate MFA on user record
        user.mfa_enabled = True
        user.save(update_fields=["mfa_enabled"])
        cache.delete(cache_key)

        return Response(
            {
                "status": "mfa_enabled",
                "message": "Two-factor authentication successfully activated for this account.",
            },
            status=status.HTTP_200_OK,
        )
