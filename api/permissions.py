"""
DRF custom permission classes for ForensicVision.

Permission matrix:
                   viewer   editor   owner   admin
read case/evidence   ✓        ✓        ✓       ✓
upload evidence      ✗        ✓        ✓       ✓
create analysis      ✗        ✓        ✓       ✓
post comment         ✓        ✓        ✓       ✓
create viewer link   ✗        ✓        ✓       ✓
create editor link   ✗        ✗        ✓       ✓
revoke link          ✗        ✗        ✓       ✓
edit/delete case     ✗        ✗        ✓       ✓
"""
from rest_framework.permissions import BasePermission, SAFE_METHODS

from core.models import Case, CaseMembership


def _get_case(obj) -> Case | None:
    """Attempt to extract a Case instance from arbitrary objects."""
    if isinstance(obj, Case):
        return obj
    if hasattr(obj, "case"):
        return obj.case
    return None


def _membership(user, case: Case) -> CaseMembership | None:
    if not case:
        return None
    return CaseMembership.objects.filter(case=case, user=user).first()


def _is_owner(user, case: Case) -> bool:
    return case is not None and case.created_by_id == user.pk



class IsCaseMember(BasePermission):
    """
    Read access: viewer, editor, owner, admin.
    Write access: editor, owner, admin.
    Object-level: only if user is a member of that case.
    """
    message = "Нямате достъп до този кейс."

    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        case = _get_case(obj)
        if case is None:
            return False
        if _is_owner(request.user, case):
            return True
        m = _membership(request.user, case)
        if m is None:
            return False
        # Safe methods → viewer is enough
        if request.method in SAFE_METHODS:
            return True
        # Mutating → editor or above
        return m.role == CaseMembership.Role.EDITOR


class IsEditorOrOwnerOrAdmin(BasePermission):
    """Requires editor role or above on the related case."""
    message = "Само редактори и собственици могат да извършат това действие."

    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        case = _get_case(obj)
        if _is_owner(request.user, case):
            return True
        m = _membership(request.user, case)
        return m is not None and m.role == CaseMembership.Role.EDITOR


class IsOwnerOrAdmin(BasePermission):
    """Requires case ownership or admin."""
    message = "Само собственикът на кейса може да извърши това действие."

    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        case = _get_case(obj)
        return _is_owner(request.user, case)
