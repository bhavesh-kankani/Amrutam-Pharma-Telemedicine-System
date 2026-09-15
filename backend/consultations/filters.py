import django_filters
from django.db import models
from django.utils import timezone
from accounts.models import Doctor
from .models import AvailabilitySlot, AvailabilitySlotStatus


class DoctorFilter(django_filters.FilterSet):
    """
    FilterSet for searching and filtering verified doctors.
    - specialization: case-insensitive partial match
    - min_fee: minimum consultation fee (gte)
    - max_fee: maximum consultation fee (lte)
    - availability_date: filter doctors having active/available slots on a specific date (YYYY-MM-DD)
    """

    specialization = django_filters.CharFilter(
        method="filter_specialization",
        help_text="Search doctors by specialization (case-insensitive substring and term variants)",
    )
    min_fee = django_filters.NumberFilter(
        field_name="consultation_fee",
        lookup_expr="gte",
        help_text="Filter doctors with consultation fee greater than or equal to this amount",
    )
    max_fee = django_filters.NumberFilter(
        field_name="consultation_fee",
        lookup_expr="lte",
        help_text="Filter doctors with consultation fee less than or equal to this amount",
    )
    availability_date = django_filters.DateFilter(
        method="filter_availability_date",
        help_text="Filter doctors who have active available slots on the specified date (YYYY-MM-DD)",
    )

    class Meta:
        model = Doctor
        fields = ["specialization", "min_fee", "max_fee", "availability_date"]

    def filter_specialization(self, queryset, name, value):
        if not value:
            return queryset
        val = value.strip()

        # 1. Exact case-insensitive substring
        qs = queryset.filter(specialization__icontains=val)
        if qs.exists():
            return qs

        # 2. Domain root stem matching (e.g. 'ayurveda' <-> 'ayurvedic')
        lowered = val.lower()
        variants = {val}
        if lowered.endswith("da"):
            stem = lowered[:-1]  # ayurveda -> ayurved
            variants.add(stem)
            variants.add(stem + "ic")
        elif lowered.endswith("ic"):
            stem = lowered[:-2]  # ayurvedic -> ayurved
            variants.add(stem)
            variants.add(stem + "a")
        elif lowered.endswith("s"):
            variants.add(lowered[:-1])

        query = models.Q()
        for v in variants:
            if len(v) >= 3:
                query |= models.Q(specialization__icontains=v)

        matched = queryset.filter(query)
        return matched if matched.exists() else qs

    def filter_availability_date(self, queryset, name, value):
        if not value:
            return queryset

        now = timezone.now()
        # Find doctors with slots on this date that are available or unconfirmed expired holds (self-healing)
        available_doctor_ids = (
            AvailabilitySlot.objects.filter(start_time__date=value)
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
            .values_list("doctor_id", flat=True)
            .distinct()
        )

        return queryset.filter(id__in=available_doctor_ids)
