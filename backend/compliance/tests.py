import uuid
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.contrib.admin.sites import AdminSite
from compliance.models import AuditLog
from compliance.admin import AuditLogAdmin
from accounts.models import Role

User = get_user_model()


class ComplianceAuditLogTests(TestCase):
    def test_audit_log_creation(self):
        user = User.objects.create_user(
            email="auditor@example.com",
            password="Password123!",
            role=Role.ADMIN,
        )
        entity_uuid = uuid.uuid4()
        log = AuditLog.objects.create(
            actor=user,
            action="VIEW_MEDICAL_RECORD",
            entity_type="prescriptions",
            entity_id=entity_uuid,
            changes={"viewed_section": "medications"},
            ip_address="192.168.1.100",
        )
        self.assertEqual(log.actor, user)
        self.assertEqual(log.action, "VIEW_MEDICAL_RECORD")
        self.assertEqual(log.entity_type, "prescriptions")
        self.assertEqual(log.entity_id, entity_uuid)
        self.assertIn("viewed_section", log.changes)
        self.assertEqual(log.ip_address, "192.168.1.100")

    def test_audit_log_admin_immutability(self):
        site = AdminSite()
        admin_obj = AuditLogAdmin(AuditLog, site)

        # Audit logs must never allow add, change, or delete from admin interface
        self.assertFalse(admin_obj.has_add_permission(request=None))
        self.assertFalse(admin_obj.has_change_permission(request=None))
        self.assertFalse(admin_obj.has_delete_permission(request=None))
