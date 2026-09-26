"""Fractional ordering for kanban columns.

Leads, cases, tasks and opportunities each carry a ``kanban_order`` and each
grew its own copy of the placement maths. The copies drifted: only one excluded
the card being moved, none of them looked at the card above a ``below_id``
neighbour (so a drop between two adjacent cards landed exactly on one of them),
none checked that a neighbour belonged to the destination column, and none had a
way back once the gaps collapsed. This module is the single copy.

The caller passes a queryset already narrowed to the destination column, which
is what makes the neighbour handling safe: neighbours are looked up *inside*
that queryset, so an id naming a card in another column, another org, or nothing
at all is simply not found and placement falls through to append. There is no
separate validation step to forget.
"""

from decimal import Decimal

from django.shortcuts import get_object_or_404

from common.models import Org

#: Gap left between cards when appending or offsetting past the end.
STRIDE = Decimal("1000")

#: Rebalance rather than subdivide once neighbours are closer than this.
#: ``kanban_order`` stores 6 decimal places, so two cards nearer than 1e-6
#: cannot both be represented; this leaves an order of magnitude of headroom.
MIN_GAP = Decimal("0.00001")


def _order_of(column_qs, pk):
    """The order of ``pk`` if it is in this column, else ``None``."""
    if not pk:
        return None
    row = column_qs.filter(pk=pk).only("kanban_order").first()
    return row.kanban_order if row else None


def _neighbour(column_qs, order, *, below):
    """The order of the card immediately below or above ``order``."""
    if below:
        qs = column_qs.filter(kanban_order__gt=order).order_by("kanban_order")
    else:
        qs = column_qs.filter(kanban_order__lt=order).order_by("-kanban_order")
    return qs.values_list("kanban_order", flat=True).first()


def rebalance_column(column_qs):
    """Renumber a column to clean ``STRIDE`` multiples, preserving its order.

    Cards are taken in ``(kanban_order, -created_at)``, the same tie-break the
    board GETs read with, so a column with duplicate orders renumbers to the
    sequence the user was already looking at. Written with ``bulk_update`` so
    the models' own ``save()`` bookkeeping (probability, stage timestamps) does
    not fire on what is purely a renumbering.
    """
    changed = []
    for position, row in enumerate(
        column_qs.order_by("kanban_order", "-created_at"), 1
    ):
        target = STRIDE * position
        if row.kanban_order != target:
            row.kanban_order = target
            changed.append(row)
    if changed:
        column_qs.model.objects.bulk_update(changed, ["kanban_order"])


def place_in_column(
    column_qs, *, above_id=None, below_id=None, explicit=None, exclude_pk=None
):
    """Return the ``kanban_order`` for a card landing in ``column_qs``.

    ``column_qs`` must already be filtered to the destination column (org, plus
    stage or status). ``above_id``/``below_id`` are the ids of the cards the
    client dropped between; either, both, or neither may be given, and any that
    do not name a card in this column are ignored.

    May renumber the column as a side effect: when the two neighbours have
    collapsed to within ``MIN_GAP`` there is no representable value between
    them, so the column is rebalanced and the placement recomputed.
    """
    if explicit is not None:
        return explicit
    if exclude_pk is not None:
        column_qs = column_qs.exclude(pk=exclude_pk)

    above = _order_of(column_qs, above_id)
    below = _order_of(column_qs, below_id)

    # Fill in whichever side the client left out, so a one-sided hint still
    # places *between* two real cards instead of guessing a stride past one.
    if above is not None and below is None:
        below = _neighbour(column_qs, above, below=True)
    elif below is not None and above is None:
        above = _neighbour(column_qs, below, below=False)

    if above is not None and below is not None:
        if below - above < MIN_GAP:
            rebalance_column(column_qs)
            return place_in_column(column_qs, above_id=above_id, below_id=below_id)
        return (above + below) / 2
    if above is not None:
        return above + STRIDE
    if below is not None:
        return below - STRIDE

    last = (
        column_qs.order_by("-kanban_order")
        .values_list("kanban_order", flat=True)
        .first()
    )
    return last + STRIDE if last is not None else STRIDE


def make_only_default(model, org, validated_data, keep_pk=None):
    """Clear the org's other default ``model`` pipeline before one takes it.

    Lead, ticket and task pipelines each allow one default per org
    (a conditional unique constraint), so saving a second default was an
    IntegrityError and a 500. Taking it demotes the old one, deleted pipelines
    included: one deleted while still the default would otherwise hold the
    slot for good, since a deleted pipeline answers 404 to every verb.

    Two admins taking the default at once would each demote before the other
    committed, and the second save would hit the constraint. So the org's row
    is locked first and the two take turns: the second demotes only after the
    first has committed, and so demotes it. The org row rather than the
    pipeline rows, because an org with no pipeline yet has no row to lock and
    its first two defaults race all the same. An update or delete has already
    taken it through `lock_pipeline`, and taking it again is a no-op. The
    caller holds the transaction; every caller is `transaction.atomic`.
    """
    if not validated_data.get("is_default"):
        return
    Org.objects.select_for_update().filter(pk=org.pk).first()
    model.objects.filter(org=org, is_default=True).exclude(pk=keep_pk).update(
        is_default=False
    )


def lock_pipeline(model, org, pk):
    """The live ``model`` pipeline ``pk`` of ``org``, locked for the caller's
    transaction, or the 404 a missing id gets.

    Pipeline update and delete read the row and then save all of it, so a
    rename saved from a read taken before another admin made the pipeline the
    default wrote the old `is_default` back, and the org was left with no
    default; a delete could be undone the same way. The row is read under a
    lock instead, and the org's row is locked first, the lock
    `make_only_default` takes. So every pipeline write locks the org and then
    the pipeline, one order everywhere, and two writes cannot each hold what
    the other waits for. Call it inside `transaction.atomic`.
    """
    Org.objects.select_for_update().filter(pk=org.pk).first()
    return get_object_or_404(
        model.objects.select_for_update(), pk=pk, org=org, is_active=True
    )
