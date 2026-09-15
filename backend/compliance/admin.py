from django.contrib import admin
from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "timestamp",
        "actor",
        "action",
        "entity_type",
        "entity_id",
        "ip_address",
    )
    list_filter = ("action", "entity_type", "timestamp")
    search_fields = (
        "action",
        "entity_type",
        "entity_id",
        "actor__email",
        "ip_address",
    )
    readonly_fields = (
        "id",
        "actor",
        "action",
        "entity_type",
        "entity_id",
        "changes",
        "ip_address",
        "timestamp",
    )

    def has_add_permission(self, request):
        # Audit logs can only be created programmatically by system operations
        return False

    def has_change_permission(self, request, obj=None):
        # Audit logs are immutable records
        return False

    def has_delete_permission(self, request, obj=None):
        # Prevent deletion through Django admin to maintain compliance integrity
        return False
