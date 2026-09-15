import uuid
from django.db import models
from django.utils import timezone
from django.conf import settings


class AuditLog(models.Model):
    """
    Security and compliance audit trail tracking all PHI access,
    administrative actions, and critical state modifications.
    Designed for monthly range partitioning in PostgreSQL.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
        db_column="actor_id",
    )
    action = models.CharField(max_length=100, db_index=True)
    entity_type = models.CharField(max_length=50, db_index=True)
    entity_id = models.UUIDField(db_index=True)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True, editable=False)

    class Meta:
        db_table = "audit_logs"
        verbose_name = "Audit Log"
        verbose_name_plural = "Audit Logs"
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["action", "timestamp"]),
        ]

    def __str__(self):
        actor_label = self.actor.email if self.actor else "SYSTEM"
        return f"[{self.timestamp:%Y-%m-%d %H:%M:%S}] {actor_label} -> {self.action} on {self.entity_type}:{self.entity_id}"
