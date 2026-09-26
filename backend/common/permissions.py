"""
Custom Permission Classes for Multi-Tenancy Security

These permission classes enforce organization context and access control
across all API endpoints.
"""

from rest_framework import permissions


def is_org_admin(profile):
    """Whether ``profile`` administers its org: ``role == "ADMIN"``, or the
    profile belongs to a Django superuser.

    Superusers are org admins everywhere (owner decision, 1.11.0). Before that,
    this read ``role`` only while dozens of call sites added ``or
    request.user.is_superuser`` by hand and the rest did not, so a superuser
    holding the USER role could change a settings page at one endpoint and was
    refused at the next, and both clients showed it read-only. One rule, here.

    Membership is still required. This takes a ``Profile``, which is one
    user's membership of one org, and every request's profile is resolved by
    the middleware from an active ``Profile`` row for the org in the signed
    token. A superuser with no profile in an org gets no profile there, so
    this function is never asked about that org. ``is_superuser`` itself is
    not writable through any API serializer; it is granted with
    ``createsuperuser`` or the Django admin.

    A plain function and not only the ``IsOrgAdmin`` class below, because most
    callers are views that read wide and write narrow: the same endpoint is
    open to every member on GET and admin-only on POST, so the check has to
    happen inside the method rather than in ``permission_classes``.

    **This deliberately does not consult ``is_organization_admin``.** That
    column mirrors ``role`` (``Profile.save`` derives it) and is not an input
    anywhere; it used to be, and an admin could ``PATCH`` a colleague to
    ``{"is_organization_admin": true, "role": "USER"}`` and grant an admin the
    UI could neither show nor revoke. The API field of the same name is
    computed from this function (see ``common.serializer``), not read from the
    column.

    ``is_superuser`` must be literally ``True``. A test double or a partially
    loaded object answering some other truthy value is not an admin.

    ``None`` is not an admin. A view with no org context has no profile, and
    answering ``False`` gives it a clean 403 instead of a 500.
    """
    if profile is None:
        return False
    if getattr(profile, "role", None) == "ADMIN":
        return True
    user = getattr(profile, "user", None)
    return getattr(user, "is_superuser", False) is True


def can_mass_import(profile):
    """Whether ``profile`` may bulk-create records through a CSV import.

    Org admins (``is_org_admin``, so Django superusers too) and members
    granted ``has_sales_access``.
    Everyone else can still create records one at a time; the import is the
    mass-create surface, so it is gated more narrowly. One rule for the
    contact, ticket and lead importers, which used to carry three copies of
    it, none of which admitted a superuser.
    """
    if profile is None:
        return False
    if is_org_admin(profile):
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
    Permission class that requires an org admin, as ``is_org_admin`` defines it
    (ADMIN role, or a superuser's profile in this org).

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
