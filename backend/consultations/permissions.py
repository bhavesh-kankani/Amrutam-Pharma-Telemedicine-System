from rest_framework.permissions import BasePermission


class CanBookConsultation(BasePermission):
    """
    Allows booking if the user is authenticated and is NOT an admin.
    Both standard patients and fellow doctors can book consultations.
    """

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request.user, "role", None) in ["patient", "doctor"]
        )
