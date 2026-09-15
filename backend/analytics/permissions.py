from rest_framework.permissions import BasePermission


class IsAdminRole(BasePermission):
    """
    Allows access only to authenticated users possessing the 'admin' role or superuser status.
    """

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (getattr(request.user, "role", None) == "admin" or request.user.is_superuser)
        )
