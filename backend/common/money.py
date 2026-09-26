"""Money totals that never add two currencies together.

Invoices, estimates and deals each carry their own currency and there is no
exchange-rate data, so a figure over several records is only meaningful per
currency. Every endpoint that totals money shapes it the same way, through
``currency_block``: the plain figure it has always returned when at most one
currency is present, ``None`` when there are several, and a ``by_currency``
list either way.
"""

from django.db.models import F, Value
from django.db.models.functions import Coalesce, NullIf


def org_currency(org):
    """The org's default currency, or USD for an org that has none set."""
    return org.default_currency or "USD"


def deal_currency(org):
    """The currency a deal counts in: its own, or the org's default when blank.

    The deal serializer fills a missing currency from the org's default on
    create, so a blank one on an older row means the same thing.
    """
    return Coalesce(NullIf("currency", Value("")), Value(org_currency(org)))


def group_by_currency(queryset, groups=None, currency=None, **aggregates):
    """Fold ``aggregates`` over ``queryset`` into ``{currency: {name: value}}``.

    ``currency`` is the expression to group on, the row's own ``currency``
    field by default. Pass ``groups`` to merge a second queryset's figures into
    the same map.
    """
    groups = {} if groups is None else groups
    code = F("currency") if currency is None else currency
    rows = queryset.values(currency_code=code).annotate(**aggregates).order_by()
    for row in rows:
        groups.setdefault(row.pop("currency_code"), {}).update(row)
    return groups


def currency_block(groups, money=(), counts=()):
    """Shape ``{currency: {name: value}}`` for a response.

    The block holds ``by_currency`` (one entry per currency, ordered by code)
    and, for each money field, the amount itself when at most one currency is
    present and ``None`` when there are several. Count fields are totalled,
    since a count has no currency.
    """
    rows = [
        {
            "currency": code,
            **{f: groups[code].get(f) or 0 for f in counts},
            **{f: str(groups[code].get(f) or 0) for f in money},
        }
        for code in sorted(groups)
    ]
    block = {
        f: rows[0][f] if len(rows) == 1 else (None if rows else "0") for f in money
    }
    block.update({f: sum(row[f] for row in rows) for f in counts})
    block["by_currency"] = rows
    return block
