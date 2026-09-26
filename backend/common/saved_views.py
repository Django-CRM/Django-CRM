"""Saved list views (G29): which lists can have one, and what a view may hold.

A saved view is a name and a set of list query parameters, stored per profile
so a person can put a filtered list back with one tap. Only the six lists that
also export to CSV take one.

**What a view may hold is what its list reads.** Each entry in :data:`LISTS`
pairs the function the list view (and its CSV export) builds its queryset
with, and the query parameters that function reads. A view is checked two
ways before it is stored:

* every key is one of those parameters, or a ``cf_<key>`` custom-field filter,
  which every one of these lists takes. Anything else is refused, so a view
  cannot become a place to park arbitrary data;
* the values are handed to that same function, so a malformed id, date or
  number is refused with the list's own 400 now, rather than taking the list
  down every time the view is applied.

The parameter sets are written out rather than discovered, so a change to what
a view can hold shows up in a diff. ``common/tests/test_saved_views.py`` reads
each list function with a recording mapping and fails when the two disagree,
in either direction.
"""

import re

from django.db import IntegrityError, transaction
from django.http import QueryDict
from rest_framework import serializers

from accounts.views import account_list_queryset
from cases.views import case_list_queryset
from common.models import SavedView
from contacts.views import contact_list_queryset
from invoices.api_views import filter_invoices
from invoices.permissions import visible_invoices_qs
from leads.views.lead_views import lead_list_queryset
from opportunity.views.opportunity_views import deal_list_queryset

MAX_VIEWS_PER_LIST = 25
MAX_PARAMS = 30
MAX_VALUES_PER_PARAM = 50
MAX_VALUE_LENGTH = 200

CUSTOM_FIELD_PARAM = re.compile(r"^cf_[A-Za-z0-9_]{1,100}$")

_DATES = ("created_at__gte", "created_at__lte")

LISTS = {
    "leads": (
        lambda request, params: lead_list_queryset(
            request.profile, request.user, params
        ),
        frozenset(
            {
                "name",
                "salutation",
                "source",
                "assigned_to",
                "status",
                "tags",
                "city",
                "email",
                "rating",
                "search",
                *_DATES,
                "close_date__gte",
                "close_date__lte",
                "next_follow_up",
                "open",
            }
        ),
    ),
    "contacts": (
        lambda request, params: contact_list_queryset(request.profile, params)[1],
        frozenset(
            {
                "name",
                "city",
                "phone",
                "email",
                "assigned_to",
                "tags",
                "search",
                *_DATES,
                "is_active",
            }
        ),
    ),
    "accounts": (
        lambda request, params: account_list_queryset(
            request.profile, request.user, params
        ),
        frozenset(
            {
                "name",
                "city",
                "industry",
                "tags",
                "assigned_to",
                "search",
                *_DATES,
                "is_active",
            }
        ),
    ),
    "opportunities": (
        lambda request, params: deal_list_queryset(
            request.profile, request.user, params
        ),
        frozenset(
            {
                "name",
                "account",
                "pipeline",
                "stage",
                "lead_source",
                "tags",
                "assigned_to",
                "search",
                *_DATES,
                "closed_on__gte",
                "closed_on__lte",
                "amount__gte",
                "amount__lte",
                "open",
                "rotten",
            }
        ),
    ),
    "cases": (
        lambda request, params: case_list_queryset(request.profile, params),
        frozenset(
            {
                "include_deleted",
                "show_merged",
                "name",
                "status",
                "priority",
                "account",
                "case_type",
                "assigned_to",
                "tags",
                "search",
                *_DATES,
                "sla_breached",
                "ordering",
            }
        ),
    ),
    "invoices": (
        lambda request, params: filter_invoices(
            visible_invoices_qs(request.profile, request.user), params
        ),
        frozenset(
            {
                "search",
                "status",
                "account",
                "contact",
                "opportunity",
                "assigned_to",
                "created_by",
                "issue_date_gte",
                "issue_date_lte",
                "due_date_gte",
                "due_date_lte",
                "sort",
            }
        ),
    ),
}


def _clean_filters(module, value):
    """``value`` as ``{param: [text, ...]}``, or raise with what is wrong.

    A value may arrive as one string or a list of them; blanks are dropped, and
    a parameter left with nothing is dropped with them.
    """
    if not isinstance(value, dict):
        raise serializers.ValidationError("Send the filters as an object.")
    if len(value) > MAX_PARAMS:
        raise serializers.ValidationError(
            f"A view can hold at most {MAX_PARAMS} filters."
        )
    accepted = LISTS[module][1]
    unknown = sorted(
        str(key)[:50]
        for key in value
        if not isinstance(key, str)
        or (key not in accepted and not CUSTOM_FIELD_PARAM.match(key))
    )
    if unknown:
        raise serializers.ValidationError(
            f"This list does not filter by: {', '.join(unknown[:5])}."
        )
    cleaned = {}
    for key, raw in value.items():
        values = raw if isinstance(raw, list) else [raw]
        if not all(isinstance(v, str) for v in values):
            raise serializers.ValidationError(f"{key}: every value must be text.")
        values = [v.strip() for v in values if v.strip()]
        if len(values) > MAX_VALUES_PER_PARAM:
            raise serializers.ValidationError(
                f"{key}: at most {MAX_VALUES_PER_PARAM} values."
            )
        if any(len(v) > MAX_VALUE_LENGTH for v in values):
            raise serializers.ValidationError(
                f"{key}: a value can be at most {MAX_VALUE_LENGTH} characters."
            )
        if values:
            cleaned[key] = values
    return cleaned


def _check_values(request, module, filters):
    """Run the list's own parameter parsing over ``filters``.

    The queryset is built, never evaluated. A malformed id, date or number
    raises the list's own ``ValidationError`` while it is being built.
    """
    params = QueryDict(mutable=True)
    for key, values in filters.items():
        params.setlist(key, values)
    try:
        LISTS[module][0](request, params)
    except serializers.ValidationError as exc:
        # The list names the parameter it refused; kept as one flat sentence
        # per parameter, which is what both clients show.
        detail = exc.detail
        if isinstance(detail, dict):
            detail = [
                f"{key}: {value[0] if isinstance(value, list) else value}"
                for key, value in detail.items()
            ]
        raise serializers.ValidationError({"filters": detail}) from None


class SavedViewSerializer(serializers.ModelSerializer):
    """One saved view, as its owner reads and writes it.

    ``org`` and ``profile`` are set by the view from the request, and a body
    naming either is refused rather than ignored. ``module`` is fixed once the
    view exists: a view's filters only mean something to the list they came
    from.
    """

    class Meta:
        model = SavedView
        fields = ("id", "module", "name", "filters", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Give the view a name.")
        return value

    def validate(self, attrs):
        for field in ("org", "profile"):
            if field in self.initial_data:
                raise serializers.ValidationError({field: "This is set by the server."})
        instance = self.instance
        if instance is not None and attrs.get("module", instance.module) != (
            instance.module
        ):
            raise serializers.ValidationError(
                {"module": "A saved view cannot move to another list."}
            )
        module = attrs.get("module") or instance.module
        if "filters" in attrs:
            try:
                attrs["filters"] = _clean_filters(module, attrs["filters"])
            except serializers.ValidationError as exc:
                raise serializers.ValidationError({"filters": exc.detail}) from None
            _check_values(self.context["request"], module, attrs["filters"])

        profile = self.context["request"].profile
        mine = SavedView.objects.filter(org=profile.org, profile=profile, module=module)
        if instance is not None:
            mine = mine.exclude(pk=instance.pk)
        elif mine.count() >= MAX_VIEWS_PER_LIST:
            raise serializers.ValidationError(
                f"You can save at most {MAX_VIEWS_PER_LIST} views for one list."
            )
        name = attrs.get("name")
        if name is not None and mine.filter(name__iexact=name).exists():
            raise serializers.ValidationError(
                {"name": "You already have a view with this name for this list."}
            )
        return attrs

    def save(self, **kwargs):
        # Two saves racing past the check above meet the unique constraint;
        # answered as the same 400, not a 500.
        try:
            with transaction.atomic():
                return super().save(**kwargs)
        except IntegrityError:
            raise serializers.ValidationError(
                {"name": "You already have a view with this name for this list."}
            ) from None
