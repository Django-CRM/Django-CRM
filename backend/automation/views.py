"""The automation rules API. Admin only for every method, reads included.

Routes (all under /api/automation/):
    GET    catalogue/           what the web and phone editors are built from
    GET    rules/               the org's rules with their status, and the plan hook's summary
    POST   rules/               create, after the last rule
    POST   rules/reorder/       a new list order
    GET    rules/<id>/          one rule
    PATCH  rules/<id>/          change it (no PUT)
    DELETE rules/<id>/          delete it and its runs
    POST   rules/<id>/try/      the steps a run would take on one record, writing nothing
    GET    rules/<id>/runs/     its runs, newest first

A rule acts with its owner's reach, so switching one on (a create or PATCH that
ends active from off or paused) asks the plan first and then makes the caller
its owner. Another org's id answers 404, never 403.
"""

import uuid
from collections.abc import Mapping
from copy import copy
from datetime import timedelta
from urllib.parse import urlsplit

from django.db.models import Count, Max, Q
from django.http import Http404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from automation.conditions import OPERATORS, UPDATE_ONLY
from automation.engine import _may_act, try_rule
from automation.limits import MAX_ACTIONS, MAX_CONDITIONS
from automation.links import link_specs
from automation.models import DONE, FAILED, AutomationRule, AutomationRun
from automation.objects import adapter_for, automatable, switched_off_module
from automation.ownership import SENTENCES, owner_problem
from automation.plan import may_switch_on, runnable
from automation.serializers import (
    RULE_KEYS,
    AutomationStepSerializer,
    RuleRowSerializer,
    RuleSerializer,
    RunSerializer,
)
from automation.validation import active_people, offered_actions, validate_rule
from common.lookups import get_scoped_or_404
from common.modules import module_label
from common.pagination import CappedLimitOffsetPagination
from common.permissions import HasOrgContext, IsOrgAdmin
from webhooks.models import JSON, WebhookEndpoint

TAGS = ["Automation"]
OVER_LIMIT = "Over your plan's limit"
NEEDS_ATTENTION = "Needs attention"
PAUSED = "Paused"
RECENT = timedelta(days=7)

# The type each rule key is stored as. The plan judges a switch-on before the
# body is validated, so it only sees body values of these types.
RULE_TYPES = {
    "object_key": str,
    "trigger": dict,
    "match": str,
    "conditions": list,
    "actions": list,
}


def _invalid(errors):
    return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)


def _context(request):
    return {"org": request.profile.org}


def _switches_on(rule, data) -> bool:
    """Whether the body turns `rule` (None for a new one) on from off or paused.

    An active rule whose owner can no longer run it counts as paused, as the
    list shows it. Read as the serializer's BooleanField reads it, so `"true"`
    counts too.
    """
    if rule is not None and rule.is_active and _may_act(rule):
        return False
    if not isinstance(data, Mapping):
        return False
    try:
        return data.get("is_active") in serializers.BooleanField.TRUE_VALUES
    except TypeError:
        return False


def _as_switched_on(rule, data):
    """An unsaved copy of `rule` as the body would leave it, switched on."""
    candidate = copy(rule)
    if isinstance(data, Mapping):
        for key, kind in RULE_TYPES.items():
            if isinstance(data.get(key), kind):
                setattr(candidate, key, data[key])
    candidate.is_active = True
    return candidate


def _plan_refusal(org, rule, data):
    """The 403 the plan answers for switching `rule` on, or None."""
    refusal = may_switch_on(org, _as_switched_on(rule, data))
    if refusal is None:
        return None
    return Response(
        {"detail": refusal, "code": "plan_limit"}, status=status.HTTP_403_FORBIDDEN
    )


def _owned_by(profile):
    return {"owner": profile, "switched_on_at": timezone.now(), "paused_reason": ""}


def _status(org, rule, plan):
    """`(status, status_text)` for one rule; `plan` is `runnable`'s answer."""
    if not rule.is_active:
        if rule.paused_reason:
            return "paused", SENTENCES.get(rule.paused_reason, PAUSED)
        return "off", "Off"
    # An owner changed by a path that skips the pausing signals.
    if not _may_act(rule):
        return "paused", SENTENCES.get(owner_problem(rule), PAUSED)
    # The rule stays in the active-rule list passed to `runnable`; only its
    # status changes, and `is_active` is left alone so it resumes on its own.
    module = switched_off_module(org, rule.object_key)
    if module is not None:
        return "paused", f"Paused: {module_label(org, module)} is switched off"
    if rule.id not in plan.rule_ids:
        return "over_limit", plan.statuses.get(rule.id, OVER_LIMIT)
    errors = validate_rule(org, {key: getattr(rule, key) for key in RULE_KEYS})
    if errors:
        first = next(iter(errors.values()))[0]
        return "needs_attention", f"{NEEDS_ATTENTION}: {first}"
    return "on", "On"


def _recent_runs(org):
    """`{rule id: (runs, failures)}` over the last 7 days; skipped rows excluded."""
    rows = (
        AutomationRun.objects.filter(
            org=org,
            created_at__gte=timezone.now() - RECENT,
            status__in=(DONE, FAILED),
        )
        .order_by()
        .values("rule_id")
        .annotate(runs=Count("id"), failures=Count("id", filter=Q(status=FAILED)))
    )
    return {row["rule_id"]: (row["runs"], row["failures"]) for row in rows}


def _field(spec):
    return {
        "key": spec.key,
        "label": spec.label,
        "type": spec.type,
        "choices": [
            {
                "value": choice.value,
                "label": choice.label,
                "group": choice.group,
                "settable": choice.settable,
            }
            for choice in spec.choices
        ],
        "settable": spec.settable,
    }


def _object(org, entry, adapter, reachable):
    """One object of the catalogue; `reachable` maps automatable keys to entries.

    A parent or link to an object rules cannot reach is left out, as
    `validate_rule` would refuse a step aimed at it.
    """
    parent = reachable.get(adapter.parent_key) if adapter.parent_key else None
    return {
        "key": entry.key,
        "label": entry.label,
        "label_plural": entry.label_plural,
        "fields": [_field(spec) for spec in adapter.fields(org)],
        "parent": {"key": parent.key, "label": parent.label} if parent else None,
        "links": [
            {"id": spec.id, "label": spec.label, "object_key": spec.object_key}
            for spec in link_specs(org, entry.key)
            if spec.object_key in reachable
        ],
    }


def _record_or_404(org, rule, data):
    """The org's record of the rule's object that the body's `record_id` names."""
    value = data.get("record_id") if isinstance(data, Mapping) else None
    try:
        pk = uuid.UUID(str(value))
    except ValueError:
        raise Http404("No such record.") from None
    adapter = adapter_for(org, rule.object_key)
    record = adapter.load(org, pk) if adapter is not None else None
    if record is None:
        raise Http404("No such record.")
    return record


def _reordered(rules, ids):
    """`rules` in the order `ids` names them, the rest after; or an error."""
    if not isinstance(ids, list):
        return None, "Send a list of rule ids."
    by_id = {rule.id: rule for rule in rules}
    named = []
    for value in ids:
        try:
            pk = uuid.UUID(str(value))
        except ValueError:
            pk = None
        if pk not in by_id:
            return None, "Each id must be one of this org's rules."
        named.append(pk)
    if len(set(named)) != len(named):
        return None, "Name each rule once."
    rest = [rule for rule in rules if rule.id not in set(named)]
    return [by_id[pk] for pk in named] + rest, None


class RunPagination(CappedLimitOffsetPagination):
    default_limit = 20
    max_limit = 100


class AutomationView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext, IsOrgAdmin)

    def get_rule(self, request, pk):
        return get_scoped_or_404(AutomationRule, pk, request.profile.org)


class AutomationCatalogueChoiceSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()
    group = serializers.CharField()
    settable = serializers.BooleanField()


class AutomationCatalogueFieldSerializer(serializers.Serializer):
    """Schema only: one field as ``_field`` builds it."""

    key = serializers.CharField()
    label = serializers.CharField()
    type = serializers.CharField()
    choices = AutomationCatalogueChoiceSerializer(many=True)
    settable = serializers.BooleanField()


class AutomationCatalogueParentSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()


class AutomationCatalogueLinkSerializer(serializers.Serializer):
    id = serializers.CharField()
    label = serializers.CharField()
    object_key = serializers.CharField()


class AutomationCatalogueObjectSerializer(serializers.Serializer):
    """Schema only: one object as ``_object`` builds it."""

    key = serializers.CharField()
    label = serializers.CharField()
    label_plural = serializers.CharField()
    fields = AutomationCatalogueFieldSerializer(many=True)
    parent = AutomationCatalogueParentSerializer(allow_null=True)
    links = AutomationCatalogueLinkSerializer(many=True)


class AutomationCataloguePersonSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()


class AutomationCatalogueEndpointSerializer(serializers.Serializer):
    id = serializers.CharField()
    # The URL's host when the endpoint has no description; null if it has none.
    description = serializers.CharField(allow_null=True)


class AutomationCatalogueLimitsSerializer(serializers.Serializer):
    conditions = serializers.IntegerField()
    actions = serializers.IntegerField()


class CatalogueView(AutomationView):
    @extend_schema(
        tags=TAGS,
        responses={
            200: inline_serializer(
                name="AutomationCatalogueResponse",
                fields={
                    "objects": AutomationCatalogueObjectSerializer(many=True),
                    # Operator names by field type.
                    "operators": serializers.DictField(
                        child=serializers.ListField(child=serializers.CharField())
                    ),
                    "update_only_operators": serializers.ListField(
                        child=serializers.CharField()
                    ),
                    "actions": serializers.ListField(child=serializers.CharField()),
                    "people": AutomationCataloguePersonSerializer(many=True),
                    "endpoints": AutomationCatalogueEndpointSerializer(many=True),
                    "limits": AutomationCatalogueLimitsSerializer(),
                },
            )
        },
    )
    def get(self, request):
        org = request.profile.org
        pairs = automatable(org)
        reachable = {entry.key: entry for entry, _ in pairs}
        people = (
            active_people(org)
            .select_related("user")
            .order_by("user__name", "user__email")
        )
        endpoints = WebhookEndpoint.objects.filter(
            org=org, is_active=True, format=JSON, app__isnull=True
        ).order_by("created_at")
        return Response(
            {
                "objects": [
                    _object(org, entry, adapter, reachable) for entry, adapter in pairs
                ],
                "operators": OPERATORS,
                "update_only_operators": UPDATE_ONLY,
                "actions": offered_actions(org),
                "people": [
                    {"id": str(p.id), "name": p.user.name or p.user.email}
                    for p in people
                ],
                "endpoints": [
                    {
                        "id": str(e.id),
                        "description": e.description or urlsplit(e.url).hostname,
                    }
                    for e in endpoints
                ],
                "limits": {"conditions": MAX_CONDITIONS, "actions": MAX_ACTIONS},
            }
        )


class RuleListCreateView(AutomationView):
    @extend_schema(
        tags=TAGS,
        operation_id="automation_rules_list",
        responses={
            200: inline_serializer(
                name="AutomationRuleListResponse",
                fields={
                    "summary": serializers.CharField(allow_null=True),
                    "results": RuleRowSerializer(many=True),
                },
            )
        },
    )
    def get(self, request):
        org = request.profile.org
        rules = list(
            AutomationRule.objects.filter(org=org).select_related("owner__user")
        )
        plan = runnable(org, [rule for rule in rules if rule.is_active])
        recent = _recent_runs(org)
        data = RuleSerializer(rules, many=True, context=_context(request)).data
        results = []
        for rule, row in zip(rules, data, strict=True):
            state, text = _status(org, rule, plan)
            runs, failures = recent.get(rule.id, (0, 0))
            results.append(
                {
                    **row,
                    "status": state,
                    "status_text": text,
                    "failures_7d": failures,
                    "runs_7d": runs,
                }
            )
        return Response({"summary": plan.summary, "results": results})

    @extend_schema(tags=TAGS, request=RuleSerializer, responses={201: RuleSerializer})
    def post(self, request):
        org = request.profile.org
        switching_on = _switches_on(None, request.data)
        if switching_on:
            refusal = _plan_refusal(org, AutomationRule(org=org), request.data)
            if refusal is not None:
                return refusal
        serializer = RuleSerializer(data=request.data, context=_context(request))
        if not serializer.is_valid():
            return _invalid(serializer.errors)
        last = AutomationRule.objects.filter(org=org).aggregate(last=Max("position"))
        extra = {"position": 0 if last["last"] is None else last["last"] + 1}
        if switching_on:
            extra.update(_owned_by(request.profile))
        serializer.save(**extra)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class RuleReorderView(AutomationView):
    @extend_schema(
        tags=TAGS,
        request=OpenApiTypes.OBJECT,
        responses={
            200: inline_serializer(
                name="AutomationRuleReorderResponse",
                fields={"ids": serializers.ListField(child=serializers.CharField())},
            )
        },
    )
    def post(self, request):
        rules = list(AutomationRule.objects.filter(org=request.profile.org))
        data = request.data if isinstance(request.data, Mapping) else {}
        ordered, error = _reordered(rules, data.get("ids"))
        if error:
            return _invalid({"ids": [error]})
        for position, rule in enumerate(ordered):
            rule.position = position
        AutomationRule.objects.bulk_update(ordered, ["position"])
        return Response({"ids": [str(rule.id) for rule in ordered]})


class RuleDetailView(AutomationView):
    @extend_schema(tags=TAGS, responses=RuleSerializer)
    def get(self, request, pk):
        rule = self.get_rule(request, pk)
        return Response(RuleSerializer(rule, context=_context(request)).data)

    @extend_schema(tags=TAGS, request=RuleSerializer, responses=RuleSerializer)
    def patch(self, request, pk):
        org = request.profile.org
        rule = self.get_rule(request, pk)
        switching_on = _switches_on(rule, request.data)
        if switching_on:
            refusal = _plan_refusal(org, rule, request.data)
            if refusal is not None:
                return refusal
        serializer = RuleSerializer(
            rule, data=request.data, partial=True, context=_context(request)
        )
        if not serializer.is_valid():
            return _invalid(serializer.errors)
        serializer.save(**(_owned_by(request.profile) if switching_on else {}))
        return Response(serializer.data)

    @extend_schema(tags=TAGS, responses={204: None})
    def delete(self, request, pk):
        self.get_rule(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class RuleTryView(AutomationView):
    @extend_schema(
        tags=TAGS,
        request=OpenApiTypes.OBJECT,
        responses={
            200: inline_serializer(
                name="AutomationRuleTryResponse",
                fields={"steps": AutomationStepSerializer(many=True)},
            )
        },
    )
    def post(self, request, pk):
        org = request.profile.org
        rule = self.get_rule(request, pk)
        record = _record_or_404(org, rule, request.data)
        return Response({"steps": try_rule(rule, record, owner=request.profile)})


class RuleRunListView(AutomationView):
    @extend_schema(
        tags=TAGS,
        responses={
            200: inline_serializer(
                name="AutomationRunListResponse",
                fields={
                    "count": serializers.IntegerField(),
                    "results": RunSerializer(many=True),
                },
            )
        },
    )
    def get(self, request, pk):
        org = request.profile.org
        rule = self.get_rule(request, pk)
        runs = (
            AutomationRun.objects.filter(org=org, rule=rule)
            .select_related("triggered_by__rule")
            .order_by("-created_at", "-id")
        )
        paginator = RunPagination()
        page = paginator.paginate_queryset(runs, request, view=self)
        return Response(
            {
                "count": paginator.count,
                "results": RunSerializer(page, many=True).data,
            }
        )
