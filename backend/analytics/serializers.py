from rest_framework import serializers


class DailyConsultationVolumeSerializer(serializers.Serializer):
    count = serializers.IntegerField(help_text="Consultations created today")
    target = serializers.IntegerField(help_text="Target volume (100,000)")
    progress_percentage = serializers.FloatField(help_text="Percentage completed toward target")


class DoctorToSlotRatioSerializer(serializers.Serializer):
    verified_doctors = serializers.IntegerField(help_text="Count of active verified doctors")
    active_slots_today = serializers.IntegerField(help_text="Active available slots scheduled for today")
    ratio = serializers.FloatField(help_text="Ratio of verified doctors to active available slots")


class SlotBreakdownSerializer(serializers.Serializer):
    available = serializers.IntegerField(help_text="Total available slots")
    locked = serializers.IntegerField(help_text="Total held/locked slots")
    booked = serializers.IntegerField(help_text="Total booked slots")


class P95LatencySummarySerializer(serializers.Serializer):
    p95_read_target_ms = serializers.IntegerField(help_text="p95 SLA read latency in milliseconds")
    p95_write_target_ms = serializers.IntegerField(help_text="p95 SLA write latency in milliseconds")
    daily_scale_capacity = serializers.IntegerField(help_text="Daily scaling throughput target")


class AdminAnalyticsResponseSerializer(serializers.Serializer):
    daily_consultation_volume = DailyConsultationVolumeSerializer()
    total_consultations = serializers.IntegerField(help_text="All-time consultation count")
    gross_transaction_value = serializers.DecimalField(
        max_digits=14, decimal_places=2, help_text="Total sum of successful payments"
    )
    doctor_to_slot_ratio = DoctorToSlotRatioSerializer()
    slot_breakdown = SlotBreakdownSerializer()
    p95_latency_summary = P95LatencySummarySerializer()
