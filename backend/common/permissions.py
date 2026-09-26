"""
Custom Permission Classes for Multi-Tenancy Security

These permission classes enforce organization context and access control
across all API endpoints.
"""

from rest_framework import permissions


def is_org_admin(profile):
    """Whether ``profile`` administers its org. ``role`` is the only source.

    A plain function and not only the ``IsOrgAdmin`` class below, because most
    callers are views that read wide and write narrow: the same endpoint is
    open to every member on GET and admin-only on POST, so the check has to
    happen inside the method rather than in ``permission_classes``.

    That is why this rule had been written out nine times, as a private
    ``_is_admin`` in nine modules, in three different spellings. Five of them
    raised ``AttributeError`` on a ``None`` profile where two returned
    ``False``. All nine agreed for a real profile, so nothing was broken; one
    edit that missed eight copies is how that stops being true.

    **This deliberately does not consult ``is_organization_admin``.** ``ROLES``
    has exactly two members, so that column and ``role == "ADMIN"`` are two
    spellings of one binary fact, and the backend used to read them
    inconsistently: 82 checks looked only at ``role`` while 43 also accepted
    the flag, with both spellings appearing inside the same file. The flag is
    the dangerous half of that pair, because no frontend surface displays or
    sets it: an admin could ``PATCH`` a colleague to
    ``{"is_organization_admin": true, "role": "USER"}`` and grant admin over
    accounts, contacts, team management and the org record itself, invisibly
    and irrevocably from the UI. ``Profile.save`` now derives the column from
    ``role`` so the two cannot disagree, and ``ProfileSerializer`` no longer
    accepts it as an input.

    ``None`` is not an admin. A view with no org context has no profile, and
    answering ``False`` gives it a clean 403 instead of a 500.
    """
    if profile is None:
        return False
    return getattr(profile, "role", None) == "ADMIN"


def can_mass_import(profile):
    """Whether ``profile`` may bulk-create records through a CSV import.

    Org admins, Django superusers, and members granted ``has_sales_access``.
    Everyone else can still create records one at a time; the import is the
    mass-create surface, so it is gated more narrowly. One rule for the
    contact, ticket and lead importers, which used to carry three copies of
    it, none of which admitted a superuser.
    """
    if profile is None:
        return False
    if is_org_admin(profile) or profile.user.is_superuser:
        return True
    return bool(profile.has_sales_access)


class HasOrgContext(permissions.BasePermission):
    """
    Permission class that requires valid organization context.

    This should be used on all endpoints that require org-scoped data access.
    It verifies that:
    1. User is authenticated
    2. request.profile is set (from middleware)
    3. request.org is set (from JWT or API key)

    Usage:
        class MyView(APIView):
            permission_classes = [IsAuthenticated, HasOrgContext]
    """

    message = "Organization context is required. Please login again."

    def has_permission(self, request, view):
        # Must have profile set by middleware
        if not hasattr(request, "profile") or request.profile is None:
            return False

        # Must have org set
        if not hasattr(request, "org") or request.org is None:
            return False

        # Profile must be active
        if not request.profile.is_active:
            return False

        return True


class IsOrgAdmin(permissions.BasePermission):
    """
    Permission class that requires user to be an organization admin.

    Usage:
        class AdminOnlyView(APIView):
            permission_classes = [IsAuthenticated, HasOrgContext, IsOrgAdmin]
    """

    message = "You must be an organization administrator to perform this action."

    def has_permission(self, request, view):
        return is_org_admin(getattr(request, "profile", None))


class IsSuperAdmin(permissions.BasePermission):
    """
    Permission class for platform-level super admins.

    Super admin is an explicit, deliberately granted flag on the user record
    (``User.is_superuser``), never inferred from the email address. Deriving it
    from an email domain would hand platform-wide access: every org, every
    user. To anyone who can obtain an account at that domain, turning an
    ordinary signup into vertical privilege escalation.

    Grant it with ``manage.py createsuperuser``, the Django admin, or another
    audited path, not by handing out an email address.
    """

    message = "Super admin access required."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False

        return bool(user.is_active and user.is_superuser)
