"""
Parent/child case endpoints (Tier 3 parent-child).

Three endpoints over and above what `CaseDetailView` already exposes:

* ``GET  /api/cases/<pk>/tree/``: descendant tree, max depth 3.
* ``POST /api/cases/<pk>/link/``: set/clear parent with explicit audit row.
* ``POST /api/cases/<pk>/close-with-children/``: close parent and (optionally) cascade-close descendants.

The link endpoint takes a row lock on both rows so two concurrent agents cannot
build a cycle. Cascade close honours ``Org.auto_close_children_on_parent_close``
as the default, and accepts ``cascade`` in the body to override.

Who may call them follows `cases.access`, the same as the ticket detail view:

* the tree needs read on the ticket asked about, and a node the caller cannot
  read comes back redacted (see `_redacted`);
* linking needs write on the child and read on the new parent, and a parent
  the caller cannot read answers exactly like one that does not exist;
* closing needs write on the ticket and on every descendant the cascade would
  close, and each of them must pass `close_refusal`. Nothing is written unless
  every one of them does.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.access import (
    assert_case_read_access,
    assert_case_write_access,
    has_case_read_access,
    has_case_write_access,
)
from cases.approvals import close_refusal
from cases.models import Case
from cases.parent_guards import check_parent_link
from common.models import Activity
from common.permissions import HasOrgContext

TREE_MAX_DEPTH = Case.PARENT_MAX_DEPTH


def _summary(case):
    """Lightweight node payload used by the tree response."""
    return {
        "id": str(case.id),
        "name": case.name,
        "status": case.status,
        "priority": case.priority,
        "is_problem": case.is_problem,
        "is_active": case.is_active,
        "assigned_to": [str(p.id) for p in case.assigned_to.all()],
    }


def _redacted(case):
    """Node payload for a ticket the caller may not open.

    Kept in the tree rather than dropped, so a readable grandchild under a
    hidden child still sits at its real depth instead of being lost or
    re-parented. It carries the id (both clients key rows on it), ``status``
    and ``is_active`` (both clients count the open descendants a cascading
    close would take, and the close refuses when one of those is not the
    caller's), and nothing else: no name, priority or assignees.
    """
    return {
        "id": str(case.id),
        "name": None,
        "restricted": True,
        "status": case.status,
        "is_active": case.is_active,
    }


def _build_tree(case, profile, depth=0):
    """Recurse to ``TREE_MAX_DEPTH``. Returns ``{...summary, children: [...]}``.

    Each node is the full summary when ``profile`` passes the read rule on it,
    and `_redacted` otherwise.
    """
    if has_case_read_access(profile, case):
        node = _summary(case)
    else:
        node = _redacted(case)
    if depth >= TREE_MAX_DEPTH:
        node["children"] = []
        node["truncated"] = True
        return node
    children = list(case.children.all().prefetch_related("assigned_to"))
    node["children"] = [_build_tree(c, profile, depth + 1) for c in children]
    return node


def _record(case, action, metadata, actor):
    Activity.objects.create(
        user=actor,
        action=action,
        entity_type="Case",
        entity_id=case.pk,
        entity_name=str(case)[:255],
        metadata=metadata,
        org_id=case.org_id,
    )


class CaseTreeView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get(self, request, pk):
        org = request.profile.org
        case = get_object_or_404(
            Case.objects.prefetch_related("assigned_to"), id=pk, org=org
        )
        # The same answer the ticket detail GET gives: 404 outside the org,
        # 403 for a ticket in it that the caller may not open.
        assert_case_read_access(request.profile, case)
        # Return the root of the visible tree: walk up to the highest ancestor
        # in the same org so a child URL still shows the full incident.
        root = case
        seen = {root.id}
        while root.parent_id and root.parent and root.parent.org_id == org.id:
            if root.parent_id in seen:
                break
            seen.add(root.parent_id)
            root = root.parent
        return Response(
            {"root": _build_tree(root, request.profile), "focus_id": str(case.id)},
            status=status.HTTP_200_OK,
        )


class CaseLinkParentView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @transaction.atomic
    def post(self, request, pk):
        org = request.profile.org
        parent_id = request.data.get("parent_id")
        # Lock the case row so a parallel link from another agent cannot
        # race on the cycle check. We look up the parent under the same lock.
        case = Case.objects.select_for_update().filter(id=pk, org=org).first()
        if case is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        # Moving a ticket in or out of a tree is a change to it, so it takes
        # the write rule, as the detail PUT does. Unlinking included.
        assert_case_write_access(request.profile, case)

        former_parent_id = case.parent_id

        if not parent_id:
            # Detach.
            if case.parent_id is None:
                return Response(
                    {"detail": "Case is not linked to a parent."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            case.parent = None
            case._parent_audit_skip = True
            case.save(update_fields=["parent"])
            _record(
                case,
                "UNLINKED_PARENT",
                {"former_parent_id": str(former_parent_id)},
                actor=request.profile,
            )
            return Response(
                {"id": str(case.id), "parent": None},
                status=status.HTTP_200_OK,
            )

        try:
            parent = (
                Case.objects.select_for_update().filter(id=parent_id, org=org).first()
            )
        except (DjangoValidationError, ValueError, TypeError):
            # Not a UUID, so it names no case: the same answer as a missing one.
            parent = None
        # A parent the caller may not open answers exactly like a missing one.
        # Otherwise the link would confirm the ticket exists, return its name,
        # and put the caller's ticket under somebody else's.
        if parent is None or not has_case_read_access(request.profile, parent):
            return Response(
                {"parent_id": "Parent case not found in this organization."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Self-parent, merged either end, cycle and depth, all four in
        # `cases.parent_guards` so `CaseSerializer` enforces the same rules on
        # the same records. The messages are the ones this endpoint has always
        # returned; the walk now terminates on a cycle it did not create.
        refusal = check_parent_link(parent, case=case)
        if refusal:
            return Response(
                {"parent_id": refusal},
                status=status.HTTP_400_BAD_REQUEST,
            )

        case.parent = parent
        case._parent_audit_skip = True
        case.save(update_fields=["parent"])

        _record(
            case,
            "LINKED_PARENT",
            {
                "parent_id": str(parent.id),
                "former_parent_id": (
                    str(former_parent_id) if former_parent_id else None
                ),
            },
            actor=request.profile,
        )
        return Response(
            {
                "id": str(case.id),
                "parent": {
                    "id": str(parent.id),
                    "name": parent.name,
                    "status": parent.status,
                },
            },
            status=status.HTTP_200_OK,
        )


def _open_descendants(case, out=None, seen=None):
    """Collect all open (status != Closed) active descendants, depth-first.

    Recursion goes through closed children too, so an open grandchild under a
    closed child is still collected. ``seen`` stops the walk on a stored
    cycle, which would otherwise recurse until the worker fell over. Rows are
    locked for the length of the caller's transaction.
    """
    if out is None:
        out = []
    if seen is None:
        seen = {case.id}
    for child in case.children.select_for_update().prefetch_related("assigned_to"):
        if child.id in seen:
            continue
        seen.add(child.id)
        if child.status != "Closed" and child.is_active:
            out.append(child)
        _open_descendants(child, out, seen)
    return out


def _refused(errors):
    """The 400 a refused close answers.

    ``errors`` is `close_refusal`'s ``{field: message}`` for the ticket itself,
    answered in `CaseMoveView`'s per-field shape, or a sentence about the
    cascade, which is about no one field and so rides as a plain string. Both
    clients render either shape.
    """
    if isinstance(errors, dict):
        errors = {field: [msg] for field, msg in errors.items()}
    return Response(
        {"error": True, "errors": errors}, status=status.HTTP_400_BAD_REQUEST
    )


def _close(case, today):
    """Close one ticket the way the ordinary close path leaves it.

    ``closed_on`` is today. ``resolved_at`` is stamped by the
    ``case_pre_save_stamp_resolved_at`` signal on the transition into Closed,
    and is listed in ``update_fields`` so that stamp is saved.
    """
    case.status = "Closed"
    case.closed_on = today
    case.save(update_fields=["status", "closed_on", "resolved_at", "updated_at"])


def _ticket_count(n):
    return f"{n} linked ticket{'' if n == 1 else 's'}"


class CaseCloseWithChildrenView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @transaction.atomic
    def post(self, request, pk):
        org = request.profile.org
        case = Case.objects.select_for_update().filter(id=pk, org=org).first()
        if case is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        assert_case_write_access(request.profile, case)
        if case.status == "Duplicate":
            return Response(
                {"detail": "Cannot close a merged case."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        resolution_comment = (request.data.get("resolution_comment") or "").strip()
        cascade_override = request.data.get("cascade")
        if cascade_override is None:
            cascade = bool(getattr(org, "auto_close_children_on_parent_close", False))
        else:
            cascade = bool(cascade_override)

        today = timezone.localdate()

        # Every check runs before anything is written, so a refusal leaves the
        # whole tree as it was. The ticket itself takes the same close gate
        # as a PATCH; `close_refusal` passes it when it is already Closed.
        refusal = close_refusal(
            case,
            status="Closed",
            closed_on=today,
            priority=case.priority,
            case_type=case.case_type,
        )
        if refusal:
            return _refused(refusal)

        descendants = _open_descendants(case) if cascade else []

        # A cascade closes each descendant, so each one needs the write rule.
        # Counted, never named: some of them the caller cannot even open.
        not_writable = [
            d for d in descendants if not has_case_write_access(request.profile, d)
        ]
        if not_writable:
            n = len(not_writable)
            return _refused(
                f"{_ticket_count(n)} under this one {'is' if n == 1 else 'are'} "
                "not yours to close, so nothing was closed. Close this ticket "
                "on its own, or ask an admin to close them all.",
            )

        # Named: every descendant left here is one the caller may write, and
        # so may open.
        gated = [
            d
            for d in descendants
            if close_refusal(
                d,
                status="Closed",
                closed_on=today,
                priority=d.priority,
                case_type=d.case_type,
            )
        ]
        if gated:
            names = ", ".join(f'"{d.name}"' for d in gated)
            return _refused(
                f"{_ticket_count(len(gated))} under this one need an approval "
                f"before closing ({names}), so nothing was closed.",
            )

        if case.status != "Closed":
            _close(case, today)

        cascaded = []
        for child in descendants:
            _close(child, today)
            _record(
                child,
                "PARENT_CLOSED_CASCADE",
                {
                    "parent_id": str(case.id),
                    "acted_via_cascade": True,
                    "resolution_comment": resolution_comment[:1000],
                },
                actor=request.profile,
            )
            cascaded.append(str(child.id))

        return Response(
            {
                "id": str(case.id),
                "status": case.status,
                "cascaded_case_ids": cascaded,
                "resolution_comment": resolution_comment,
            },
            status=status.HTTP_200_OK,
        )
