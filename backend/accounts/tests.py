from decimal import Decimal
from django.test import TestCase
from django.db import connection
from django.contrib.auth import get_user_model
from accounts.models import Profile, Doctor, Role

User = get_user_model()


class AccountsModelTests(TestCase):
    def test_create_user(self):
        user = User.objects.create_user(
            email="patient@example.com",
            password="StrongPassword123!",
            role=Role.PATIENT,
        )
        self.assertEqual(user.email, "patient@example.com")
        self.assertEqual(user.role, Role.PATIENT)
        self.assertTrue(user.check_password("StrongPassword123!"))
        self.assertFalse(user.is_staff)

    def test_create_superuser(self):
        admin_user = User.objects.create_superuser(
            email="admin@example.com",
            password="AdminPassword123!",
        )
        self.assertEqual(admin_user.role, Role.ADMIN)
        self.assertTrue(admin_user.is_staff)
        self.assertTrue(admin_user.is_superuser)

    def test_profile_pii_encryption_at_rest(self):
        user = User.objects.create_user(
            email="pii_user@example.com",
            password="Password123!",
        )
        plain_phone = "+91 98765-43210"
        expected_normalized = "+919876543210"
        profile = Profile.objects.create(
            user=user,
            first_name="Jane",
            last_name="Doe",
            phone_number=plain_phone,
        )

        # Model instance in Python returns decrypted normalized value
        profile.refresh_from_db()
        self.assertEqual(profile.phone_number, expected_normalized)

        # Direct SQL query verifies value stored in PostgreSQL is encrypted at rest
        with connection.cursor() as cursor:
            cursor.execute("SELECT phone_number FROM profiles WHERE id = %s", [profile.id])
            raw_db_value = cursor.fetchone()[0]

        self.assertNotEqual(raw_db_value, expected_normalized)
        self.assertNotIn(expected_normalized, raw_db_value)
        self.assertTrue(len(raw_db_value) > len(expected_normalized))

    def test_phone_number_normalization_and_validation(self):
        from django.core.exceptions import ValidationError
        from accounts.validators import normalize_and_validate_phone

        # Valid Indian phone number formats
        valid_cases = [
            ("9876543210", "+919876543210"),
            ("+91 98765 43210", "+919876543210"),
            ("0091-98765-43210", "+919876543210"),
            ("(+91) 9876543210", "+919876543210"),
            ("(0) 98765 43210", "+919876543210"),
            ("9876.543.210", "+919876543210"),
            ("%2B919876543210", "+919876543210"),
        ]

        for raw_input, expected in valid_cases:
            with self.subTest(phone=raw_input):
                self.assertEqual(normalize_and_validate_phone(raw_input), expected)

        # Model level test with normalization on save
        u1 = User.objects.create_user(email="phone_save@example.com", password="Pass12345678!")
        p1 = Profile.objects.create(user=u1, first_name="A", last_name="B", phone_number="0091-98765-43210")
        self.assertEqual(p1.phone_number, "+919876543210")

        # Invalid phone numbers should raise ValidationError
        invalid_cases = [
            "5876543210",    # Starts with 5
            "04027112233",   # Landline STD code
            "98765",         # Too short
            "not-a-phone",   # Non-numeric
            "",              # Empty string
        ]

        for invalid_input in invalid_cases:
            with self.subTest(invalid_phone=invalid_input):
                with self.assertRaises(ValidationError):
                    normalize_and_validate_phone(invalid_input)

        u2 = User.objects.create_user(email="phone_invalid@example.com", password="Pass12345678!")
        with self.assertRaises(ValidationError):
            Profile.objects.create(user=u2, first_name="C", last_name="D", phone_number="5876543210")



    def test_doctor_profile_creation(self):
        doctor_user = User.objects.create_user(
            email="doctor@example.com",
            password="DoctorPass123!",
            role=Role.DOCTOR,
        )
        doctor = Doctor.objects.create(
            user=doctor_user,
            specialization="Ayurvedic Medicine",
            license_number="AYUR-2026-9988",
            consultation_fee=Decimal("150.00"),
            is_verified=True,
        )
        self.assertEqual(doctor.specialization, "Ayurvedic Medicine")
        self.assertEqual(doctor.consultation_fee, Decimal("150.00"))
        self.assertTrue(doctor.is_verified)


class UserAdminFormsTests(TestCase):
    def test_user_creation_form_password_mismatch(self):
        from accounts.admin import UserCreationForm

        data = {
            "email": "mismatch@example.com",
            "role": Role.PATIENT,
            "password": "ValidPassword123!",
            "password_confirmation": "DifferentPassword123!",
        }
        form = UserCreationForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("password_confirmation", form.errors)
        self.assertIn("Passwords do not match.", form.errors["password_confirmation"])

    def test_user_creation_form_weak_passwords(self):
        from accounts.admin import UserCreationForm

        # 1. Too short
        data = {
            "email": "short@example.com",
            "role": Role.PATIENT,
            "password": "short",
            "password_confirmation": "short",
        }
        form = UserCreationForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("password", form.errors)

        # 2. Entirely numeric
        data_numeric = {
            "email": "numeric@example.com",
            "role": Role.PATIENT,
            "password": "12345678",
            "password_confirmation": "12345678",
        }
        form_numeric = UserCreationForm(data=data_numeric)
        self.assertFalse(form_numeric.is_valid())
        self.assertIn("password", form_numeric.errors)

    def test_user_creation_form_success(self):
        from accounts.admin import UserCreationForm

        data = {
            "email": "valid_user@example.com",
            "role": Role.PATIENT,
            "password": "SecurePassword2026!",
            "password_confirmation": "SecurePassword2026!",
            "is_staff": False,
            "is_superuser": False,
        }
        form = UserCreationForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(user.email, "valid_user@example.com")
        self.assertTrue(user.check_password("SecurePassword2026!"))
        self.assertNotEqual(user.password, "SecurePassword2026!")

    def test_user_change_form_email_uniqueness(self):
        from accounts.admin import UserChangeForm

        existing = User.objects.create_user(email="original@example.com", password="Pass12345678!")
        User.objects.create_user(email="other@example.com", password="Pass12345678!")

        # Duplicate email
        form = UserChangeForm(
            instance=existing,
            data={
                "email": "other@example.com",
                "role": Role.PATIENT,
                "is_active": True,
                "is_staff": False,
                "is_superuser": False,
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)


class MFATests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="mfa_test@example.com",
            password="SecurePassword2026!",
            role=Role.PATIENT,
        )
        self.client.force_authenticate(user=self.user)

    def test_mfa_setup_and_verify_lifecycle(self):
        from accounts.mfa import generate_totp_token

        # 1. Initiate MFA setup
        setup_response = self.client.post("/api/v1/auth/mfa/setup/")
        self.assertEqual(setup_response.status_code, 200)
        data = setup_response.json()
        self.assertIn("secret", data)
        self.assertIn("otpauth_uri", data)
        self.assertEqual(len(data["recovery_codes"]), 5)
        secret = data["secret"]

        # User MFA status should still be False
        self.user.refresh_from_db()
        self.assertFalse(self.user.mfa_enabled)

        # 2. Invalid TOTP token submission fails
        fail_response = self.client.post(
            "/api/v1/auth/mfa/verify/",
            {"token": "000000"},
            format="json",
        )
        self.assertEqual(fail_response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.mfa_enabled)

        # 3. Valid TOTP token activates MFA
        valid_token = generate_totp_token(secret)
        success_response = self.client.post(
            "/api/v1/auth/mfa/verify/",
            {"token": valid_token},
            format="json",
        )
        self.assertEqual(success_response.status_code, 200)
        self.assertEqual(success_response.json()["status"], "mfa_enabled")

        # User MFA status should now be True
        self.user.refresh_from_db()
        self.assertTrue(self.user.mfa_enabled)


class ObservabilityTests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient
        self.client = APIClient()

    def test_prometheus_metrics_endpoint(self):
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        # Ensure django-prometheus metrics are present
        self.assertIn("django_http_requests", content)

    def test_request_tracing_middleware_headers(self):
        custom_id = "amrutam-trace-test-uuid-999"
        response = self.client.get("/healthz/", HTTP_X_REQUEST_ID=custom_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Request-ID"), custom_id)
        self.assertIn("X-Response-Time-MS", response.headers)

        # Auto-generation when header is omitted
        auto_response = self.client.get("/healthz/")
        self.assertEqual(auto_response.status_code, 200)
        self.assertTrue(len(auto_response.headers.get("X-Request-ID", "")) > 10)
        self.assertIn("X-Response-Time-MS", auto_response.headers)


