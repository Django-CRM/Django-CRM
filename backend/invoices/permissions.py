"""
Centralised object-level authorization for invoice- and estimate-scoped
endpoints.

Every such endpoint must answer the same two questions in the same order:

1. Does this record exist *in the caller's org*?  -> 404 if not, so that a
   caller in another org cannot probe for record IDs.
2. May this caller act on it?                     -> 403 if not.

Before this module each view hand-rolled that logic, and several
(``send``, ``mark-paid``, ``duplicate``, line items, payments, comments,
attachments) skipped step 2 entirely -- see GitHub issue #698. The estimate
views were worse: they never had step 2 at all, so any member could read,
edit, delete, convert or send *any* estimate in the org, and their list view
crashed outright for non-admins (see :func:`has_object_access`). Route every
invoice or estimate endpoint through :func:`get_invoice_or_error` /
:func:`get_estimate_or_error` rather than re-deriving the rule.
"""

from django.db.models import Q
from rest_framework import status
from rest_framework.response import Response

from common.permissions import is_org_admin
from invoices.models import Estimate, Invoice, RecurringInvoice

INVOICE_NOT_FOUND = "Invoice not found"
ESTIMATE_NOT_FOUND = "Estimate not found"
RECURRING_NOT_FOUND = "Recurring invoice not found"
PERMISSION_DENIED = "Permission denied"


def has_object_access(request, obj):
    """Return True if the caller may read and act on ``obj``.

    ``obj`` is an :class:`~invoices.models.Invoice` or
    :class:`~invoices.models.Estimate`; both carry the identical ownership
    shape (a ``created_by`` ``User`` FK from ``UserAuditModel`` plus an
    ``assigned_to`` set of ``Profile`` rows). Access is granted to org admins,
    Django superusers, the user who created the record, and any profile it is
    assigned to.

    **The two comparisons are against different types on purpose.**
    ``created_by`` is a ``User``, so it must be matched with ``request.user``;
    ``assigned_to`` holds ``Profile`` rows, so it is matched with
    ``request.profile``. Comparing ``request.profile`` to ``created_by`` -- as
    the estimate views did -- is not merely always False: because it reaches
    the query layer as ``Q(created_by=<Profile>)`` it *raised* ``ValueError``
    and turned the non-admin estimate list into a 500.
    """
    profile = request.profile

    if is_org_admin(profile) or request.user.is_superuser:
        return True

    if obj.created_by_id and obj.created_by_id == request.user.id:
        return True

    return obj.assigned_to.filter(id=profile.id).exists()


def visible_invoices_qs(profile, user):
    """Invoices ``profile`` may open, the queryset form of `has_object_access`."""
    qs = Invoice.objects.filter(org=profile.org)
    if is_org_admin(profile) or user.is_superuser:
        return qs
    return qs.filter(Q(created_by=profile.user) | Q(assigned_to=profile)).distinct()


def _get_or_error(request, model, pk, not_found, queryset=None):
    """Fetch an org-scoped record and authorize the caller against it.

    Returns ``(obj, None)`` on success, or ``(None, response)`` holding the
    404/403 the view should return. Callers must check the error first::

        obj, error = _get_or_error(request, Invoice, pk, INVOICE_NOT_FOUND)
        if error:
            return error
    """
    qs = model.objects.all() if queryset is None else queryset
    obj = qs.filter(id=pk, org=request.profile.org).first()

    if not obj:
        return None, Response(
            {"error": True, "message": not_found},
            status=status.HTTP_404_NOT_FOUND,
        )

    if not has_object_access(request, obj):
        return None, Response(
            {"error": True, "message": PERMISSION_DENIED},
            status=status.HTTP_403_FORBIDDEN,
        )

    return obj, None


# ``has_invoice_object_access`` is kept as the documented public name; the
# generic core above is what estimates reuse.
def has_invoice_object_access(request, invoice):
    """Return True if the caller may read and act on ``invoice``."""
    return has_object_access(request, invoice)


def has_estimate_object_access(request, estimate):
    """Return True if the caller may read and act on ``estimate``."""
    return has_object_access(request, estimate)


def get_invoice_or_error(request, invoice_id, queryset=None):
    """Fetch an org-scoped invoice and authorize the caller against it."""
    return _get_or_error(request, Invoice, invoice_id, INVOICE_NOT_FOUND, queryset)


def get_estimate_or_error(request, estimate_id, queryset=None):
    """Fetch an org-scoped estimate and authorize the caller against it."""
    return _get_or_error(request, Estimate, estimate_id, ESTIMATE_NOT_FOUND, queryset)


def get_recurring_or_error(request, recurring_id, queryset=None):
    """Fetch an org-scoped recurring invoice and authorize the caller against it.

    ``RecurringInvoice`` carries the identical ownership shape as ``Invoice`` and
    ``Estimate`` (``AssignableMixin`` + ``created_by``), so it reuses the same
    rule. Before this, every recurring endpoint filtered on ``org`` only -- no
    object check and no non-admin list scoping -- so any member could read,
    edit, delete, or pause/resume *any* schedule in the org.
    """
    return _get_or_error(
        request, RecurringInvoice, recurring_id, RECURRING_NOT_FOUND, queryset
    )
