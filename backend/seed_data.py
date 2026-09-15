# Seed data
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

# Ensure backend root is on Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")

import django
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from accounts.models import Profile, Doctor
from consultations.models import AvailabilitySlot

User = get_user_model()


def seed_database():
    if not settings.DEBUG:
        raise RuntimeError("CRITICAL: seed_data.py cannot be executed when DEBUG=False!")

    print("--- Seeding Database ---")

    # 1. Superuser / Admin
    admin_email = "admin@amrutam.com"
    admin, admin_created = User.objects.get_or_create(
        email=admin_email,
        defaults={
            "role": "admin",
            "is_staff": True,
            "is_superuser": True,
        },
    )
    if admin_created:
        admin.set_password("AdminPassword123!")
        admin.save()
        print(f"[+] Admin created: {admin_email} / AdminPassword123!")
    else:
        print(f"[*] Admin already exists: {admin_email}")

    Profile.objects.get_or_create(
        user=admin,
        defaults={
            "first_name": "System",
            "last_name": "Administrator",
            "phone_number": "+919999999999",
        },

    )

    # 2. Doctors
    doctors_data = [
        {
            "email": "dr.sharma@amrutam.com",
            "first_name": "Rajesh",
            "last_name": "Sharma",
            "phone": "+919811122233",
            "spec": "Ayurvedic Kayachikitsa (Internal Medicine)",
            "license": "AYU-DEL-2018-0921",
            "fee": Decimal("500.00"),
        },
        {
            "email": "dr.verma@amrutam.com",
            "first_name": "Pooja",
            "last_name": "Verma",
            "phone": "+919822233344",
            "spec": "Panchakarma & Detoxification",
            "license": "AYU-MH-2020-4412",
            "fee": Decimal("1200.00"),
        },
        {
            "email": "dr.iyer@amrutam.com",
            "first_name": "Ananth",
            "last_name": "Iyer",
            "phone": "+919833344455",
            "spec": "Shalya Tantra & Chronic Pain Care",
            "license": "AYU-KA-2016-1188",
            "fee": Decimal("900.00"),
        },
    ]

    doctor_objs = []
    for doc in doctors_data:
        u, u_created = User.objects.get_or_create(
            email=doc["email"],
            defaults={"role": "doctor"},
        )
        if u_created:
            u.set_password("DoctorPass123!")
            u.save()
            print(f"[+] Doctor user created: {doc['email']}")
        else:
            print(f"[*] Doctor user already exists: {doc['email']}")

        Profile.objects.get_or_create(
            user=u,
            defaults={
                "first_name": doc["first_name"],
                "last_name": doc["last_name"],
                "phone_number": doc["phone"],
            },
        )

        d, _ = Doctor.objects.update_or_create(
            user=u,
            defaults={
                "specialization": doc["spec"],
                "license_number": doc["license"],
                "consultation_fee": doc["fee"],
                "is_verified": True,
            },
        )
        doctor_objs.append(d)

    # 3. Patients
    patients_data = [
        ("patient.arun@amrutam.com", "Arun", "Patel", "+919711223344"),
        ("patient.sneha@amrutam.com", "Sneha", "Reddy", "+919722334455"),
        ("patient.vikram@amrutam.com", "Vikram", "Malhotra", "+919733445566"),
    ]

    for email, fn, ln, phone in patients_data:
        u, u_created = User.objects.get_or_create(
            email=email,
            defaults={"role": "patient"},
        )
        if u_created:
            u.set_password("PatientPass123!")
            u.save()
            print(f"[+] Patient created: {email}")
        else:
            print(f"[*] Patient already exists: {email}")

        Profile.objects.get_or_create(
            user=u,
            defaults={
                "first_name": fn,
                "last_name": ln,
                "phone_number": phone,
            },
        )

    # 4. Availability Slots (Slots for today and tomorrow)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    created_slots = 0
    existing_slots = 0

    for doc in doctor_objs:
        for day_offset in [0, 1]:  # Today and tomorrow
            base_date = now + timedelta(days=day_offset)
            # 3 slots per day: 10:00, 11:00, 14:00
            for hour in [10, 11, 14]:
                slot_start = base_date.replace(hour=hour)
                slot_end = slot_start + timedelta(minutes=45)

                _, slot_created = AvailabilitySlot.objects.get_or_create(
                    doctor=doc,
                    start_time=slot_start,
                    defaults={
                        "end_time": slot_end,
                        "status": "available",
                    },
                )
                if slot_created:
                    created_slots += 1
                else:
                    existing_slots += 1

    print(f"[+] Slots: {created_slots} newly created, {existing_slots} already existed.")
    print("--- Database Seeding Completed Successfully ---")


if __name__ == "__main__":
    seed_database()