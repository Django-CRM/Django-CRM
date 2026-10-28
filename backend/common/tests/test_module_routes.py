"""The route map and the refusal ``RequireOrgContext`` gives a signed-in
request to a module that is off: one 404 body for every method and credential.

The guard tests walk the live URLconf. A route under a new root of
``common_urls``, ``api_common`` or ``portal``, or under a new namespace, is
unclassified and fails; a map prefix that no live route starts with fails; and
a module route the middleware cannot see (an exempt path, the portal, an exempt
view) must be on the view-checked list. A new route under a root or namespace
already in the map takes that entry's requirement.
"""

import re
from unittest.mock import patch

import pytest
from django.urls import URLResolver, get_resolver
from rest_framework.test import APIClient

from cases.models import Case
from common.middleware.rls_context import RequireOrgContext
from common.models import Comment, PersonalAccessToken
from common.modules import (
    CORE,
    KEYS,
    ROUTE_MODULES,
    VIEW_CHECKED_ROUTES,
    Requires,
    module_off_body,
    requirement_for,
)
from common.testing import set_modules_off
from leads.models import Lead

SAMPLE_ID = "6f1c2d3e-4a5b-4c6d-8e7f-901a2b3c4d5e"
METHODS = ("get", "head", "options", "post", "put", "patch", "delete")


def _api_routes():
    """``(route, namespaces, view name)`` for every ``api/`` route, joined the
    way ``ResolverMatch.route`` and ``ResolverMatch.namespaces`` are."""
    found = []

    def walk(patterns, prefix, namespaces):
        for entry in patterns:
            route = prefix + str(entry.pattern).removeprefix("^" if prefix else "")
            if isinstance(entry, URLResolver):
                inner = (
                    (*namespaces, entry.namespace) if entry.namespace else namespaces
                )
                walk(entry.url_patterns, route, inner)
            elif route.startswith("api/"):
                name = ":".join((*namespaces, entry.name)) if entry.name else None
                found.append((route, namespaces, name))

    walk(get_resolver().url_patterns, "", ())
    return found


def test_every_api_route_belongs_to_a_module_or_core():
    unclassified = sorted(
        f"{route} {list(namespaces)}"
        for route, namespaces, _ in _api_routes()
        if requirement_for(route, namespaces) is None
    )
    assert not unclassified, (
        "API routes with no entry in common.modules (add the innermost namespace "
        "to NAMESPACE_MODULES or a prefix to ROUTE_MODULES):\n"
        + "\n".join(unclassified)
    )


def test_view_checked_routes_are_the_exempt_module_routes():
    middleware = RequireOrgContext(get_response=None)
    unseen = set()
    for route, namespaces, view_name in _api_routes():
        if requirement_for(route, namespaces) in (None, CORE):
            continue
        sample = "/" + re.sub(r"<[^>]+>", SAMPLE_ID, route)
        if (
            middleware._is_exempt(sample)
            or namespaces[:1] == ("portal",)
            or view_name in RequireOrgContext.EXEMPT_VIEW_NAMES
        ):
            unseen.add(route)
    assert unseen == VIEW_CHECKED_ROUTES


CU = "common_urls"

ROUTE_CASES = [
    ("api/opportunities/goals/", (CU, "api_opportunities"), Requires(("deals", "goals"))),
    ("api/opportunities/goals/leaderboard/", (CU, "api_opportunities"), Requires(("deals", "goals"))),
    ("api/cases/solutions/<uid:pk>/publish/", (CU, "api_cases"), Requires(("knowledge_base",))),
    ("api/cases/<uid:pk>/solutions/<uid:solution_pk>/", (CU, "api_cases"), Requires(("tickets", "knowledge_base"))),
    ("api/cases/<uid:pk>/solution-suggestions/", (CU, "api_cases"), Requires(("tickets", "knowledge_base"))),
    ("api/cases/<uid:pk>/time-entries/start/", (CU, "api_cases"), Requires(("tickets", "timesheet"))),
    ("api/cases/<uid:pk>/time-summary/", (CU, "api_cases"), Requires(("tickets", "timesheet"))),
    ("api/cases/mailboxes/<uid:pk>/", (CU, "api_cases"), Requires(any_of=("tickets", "leads"))),
    ("api/cases/inbound/email/", (CU, "api_cases"), Requires(any_of=("tickets", "leads"))),
    ("api/invoices/products/<uuid:pk>/", (CU, "api_invoices"), Requires(any_of=("invoices", "deals"))),
    ("api/invoices/from-opportunity/<uuid:opportunity_id>/", (CU, "api_invoices"), Requires(("invoices", "deals"))),
    ("api/invoices/from-time-entries/", (CU, "api_invoices"), Requires(("invoices", "timesheet"), any_of=("tickets", "projects"))),
    ("api/projects/<uid:pk>/time-entries/start/", (CU, "api_projects"), Requires(("projects", "timesheet"))),
    ("api/projects/<uid:pk>/unbilled/", (CU, "api_projects"), Requires(("projects", "invoices", "timesheet"))),
    ("api/projects/<uid:pk>/invoice-amount/", (CU, "api_projects"), Requires(("projects", "invoices"))),
    ("api/projects/<uid:pk>/retainer/bill/", (CU, "api_projects"), Requires(("projects", "invoices"))),
    ("api/projects/from-deal/<uid:deal_id>/", (CU, "api_projects"), Requires(("projects", "deals"))),
    ("api/documents/<uid:pk>/download/", (CU, "api_common"), Requires(("documents",))),
    ("api/profile/calendar-feed/", (CU, "api_common"), Requires(("tasks",))),
    ("api/public/calendar/<str:token>.ics", (CU, "api_common"), Requires(("tasks",))),
    ("api/public/csat/<str:token>/", (CU,), Requires(("tickets",))),
    ("api/org/help-center/", (CU, "api_common"), Requires(("knowledge_base",))),
    ("api/portal/cases/<uid:pk>/comment/", ("portal",), Requires(("tickets",))),
    ("api/portal/articles/suggest/", ("portal",), Requires(("knowledge_base",))),
    ("api/schema/redoc/", (), CORE),
    # One route per module namespace.
    ("api/leads/kanban/", (CU, "api_leads"), Requires(("leads",))),
    ("api/leads/sources/order/", (CU, "api_leads"), CORE),
    ("api/opportunities/<uid:pk>/", (CU, "api_opportunities"), Requires(("deals",))),
    ("api/tasks/", (CU, "api_tasks"), Requires(("tasks",))),
    ("api/boards/<uid:pk>/", (CU, "api_boards"), Requires(("tasks",))),
    ("api/cases/<uid:pk>/", (CU, "api_cases"), Requires(("tickets",))),
    ("api/business-hours/calendar/", (CU, "api_business_hours"), Requires(("tickets",))),
    ("api/macros/", (CU, "api_macros"), Requires(("tickets",))),
    ("api/time-entries/timesheet/", (CU, "api_time_entries"), Requires(("timesheet",), any_of=("tickets", "projects"))),
    ("api/projects/<uid:pk>/", (CU, "api_projects"), Requires(("projects",))),
    ("api/invoices/<uuid:pk>/", (CU, "api_invoices"), Requires(("invoices",))),
    ("api/public/help/<str:slug>/", ("public_help",), Requires(("knowledge_base",))),
    ("api/public/estimate/<str:token>/accept/", ("public_invoices",), Requires(("invoices",))),
    # Core: each root of common_urls and api_common, and core namespaces.
    ("api/activities/", (CU, "api_common"), CORE),
    ("api/api-settings/<uid:pk>/", (CU, "api_common"), CORE),
    ("api/attachments/<uid:pk>/download/", (CU, "api_common"), CORE),
    ("api/auth/magic-link/verify/", (CU, "api_common"), CORE),
    ("api/custom-fields/<uid:pk>/", (CU, "api_common"), CORE),
    ("api/dashboard/today/", (CU, "api_common"), CORE),
    ("api/devices/", (CU, "api_common"), CORE),
    ("api/notifications/read-all/", (CU, "api_common"), CORE),
    ("api/org/modules/", (CU, "api_common"), CORE),
    ("api/packs/<str:pack_id>/apply/", (CU, "api_common"), CORE),
    ("api/apps/<str:app_id>/install/", (CU, "api_common"), CORE),
    ("api/profile/tokens/<uuid:pk>/", (CU, "api_common"), CORE),
    ("api/saved-views/", (CU, "api_common"), CORE),
    ("api/search/", (CU,), CORE),
    ("api/tags/<uid:pk>/merge/", (CU, "api_common"), CORE),
    ("api/teams/", (CU, "api_common"), CORE),
    ("api/user/<uid:pk>/status/", (CU, "api_common"), CORE),
    ("api/users/get-teams-and-users/", (CU, "api_common"), CORE),
    ("api/accounts/", (CU, "api_accounts"), CORE),
    ("api/portal/login/<uuid:org_id>/request/", ("portal",), CORE),
    # Unclassified: only the innermost namespace counts, and common_urls,
    # api_common and portal classify nothing by namespace.
    ("api/newapp/things/", (CU, "api_newapp"), None),
    ("api/newroot/", (CU, "api_common"), None),
    ("api/newroot/", (CU,), None),
    ("api/portal/invoices/", ("portal",), None),
    ("api/unmounted/", (), None),
]  # fmt: skip


@pytest.mark.parametrize("route,namespaces,expected", ROUTE_CASES)
def test_route_requirements(route, namespaces, expected):
    assert requirement_for(route, namespaces) == expected


def test_route_requirements_exercise_every_prefix():
    routes = [route for route, _, _ in ROUTE_CASES]
    assert not [p for p in ROUTE_MODULES if not any(r.startswith(p) for r in routes)]


def test_every_route_prefix_matches_a_live_route():
    """A prefix left behind by a URL rename would let the route fall back to its
    namespace's requirement."""
    routes = [route for route, _, _ in _api_routes()]
    stale = [p for p in ROUTE_MODULES if not any(r.startswith(p) for r in routes)]
    assert not stale, f"ROUTE_MODULES keys no live route starts with: {stale}"


@pytest.fixture
def ticket(admin_user, org_a):
    return Case.objects.create(
        name="Printer on fire",
        status="New",
        priority="Normal",
        org=org_a,
        created_by=admin_user,
    )


def test_an_off_module_answers_one_404(admin_client, user_client, org_a):
    for client in (admin_client, user_client):
        assert client.get("/api/cases/").status_code == 200
    set_modules_off(org_a, "tickets")
    for client in (admin_client, user_client):
        response = client.get("/api/cases/")
        assert response.status_code == 404
        assert response.json() == {
            "detail": "Tickets is switched off for this organization.",
            "code": "module_off",
            "module": "tickets",
        }


def test_a_refused_request_never_sets_the_org_context(admin_client, org_a):
    """The refusal returns before ``_set_org_context``, so a refused request
    leaves no ``app.current_org`` behind."""
    with patch.object(
        RequireOrgContext, "_set_org_context", autospec=True
    ) as set_context:
        assert admin_client.get("/api/cases/").status_code == 200
        assert set_context.call_count == 1
        set_context.reset_mock()
        set_modules_off(org_a, "tickets")
        assert admin_client.get("/api/cases/").status_code == 404
        set_context.assert_not_called()


@pytest.mark.parametrize("target", ("list", "detail"))
@pytest.mark.parametrize("method", METHODS)
def test_every_method_answers_the_same_404(admin_client, org_a, ticket, method, target):
    url = "/api/cases/" if target == "list" else f"/api/cases/{ticket.id}/"
    if (method, target) == ("delete", "list"):
        assert admin_client.delete(url).status_code == 405
    set_modules_off(org_a, "tickets")
    send = getattr(admin_client, method)
    if method in ("get", "head"):
        response = send(url)
    else:
        response = send(url, {"name": "Renamed", "comment": "Hi"}, format="json")
    assert response.status_code == 404
    if method == "head":
        assert response.content == b""
    else:
        assert response.json() == module_off_body(org_a, "tickets")
    ticket.refresh_from_db()
    assert (ticket.name, ticket.is_active) == ("Printer on fire", True)
    assert not Comment.objects.filter(object_id=ticket.id).exists()


def test_a_members_admin_write_is_404_not_403(user_client, org_a, user_profile):
    url = "/api/cases/escalation-policies/"
    payload = {
        "priority": "Urgent",
        "first_response_action": "notify",
        "resolution_action": "notify",
        "first_response_target_id": str(user_profile.id),
        "resolution_target_id": str(user_profile.id),
    }
    assert user_client.post(url, payload, format="json").status_code == 403
    set_modules_off(org_a, "tickets")
    response = user_client.post(url, payload, format="json")
    assert response.status_code == 404
    assert response.json() == module_off_body(org_a, "tickets")


def test_tokens_meet_the_same_gate(admin_profile, org_a):
    raw, _ = PersonalAccessToken.generate(profile=admin_profile, name="cli")
    pat = APIClient()
    pat.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    key = APIClient()
    key.credentials(HTTP_TOKEN=org_a.api_key)
    assert pat.get("/api/leads/").status_code == 200
    assert key.get("/api/leads/").status_code == 200

    set_modules_off(org_a, "leads")
    lead = {"title": "Gate", "first_name": "Ga", "last_name": "Te"}
    for response in (
        pat.get("/api/leads/"),
        pat.post("/api/leads/", lead, format="json"),
        key.get("/api/leads/"),
    ):
        assert response.status_code == 404
        assert response.json() == module_off_body(org_a, "leads")
    assert not Lead.objects.filter(org=org_a).exists()
    # The key is read-only: its scope refusal still comes first.
    assert key.post("/api/leads/", lead, format="json").status_code == 403


def test_a_need_names_the_module_that_is_off(admin_client, org_a, ticket):
    suggestions = f"/api/cases/{ticket.id}/solution-suggestions/?q=x"
    cases = (
        ("deals", "/api/opportunities/goals/", "deals"),
        ("goals", "/api/opportunities/goals/", "goals"),
        ("timesheet", "/api/time-entries/timesheet/", "timesheet"),
        ("knowledge_base", suggestions, "knowledge_base"),
    )
    for off, url, named in cases:
        set_modules_off(org_a)
        assert admin_client.get(url).status_code == 200, url
        set_modules_off(org_a, off)
        response = admin_client.get(url)
        assert response.status_code == 404, url
        assert response.json() == module_off_body(org_a, named)
    assert admin_client.get(f"/api/cases/{ticket.id}/").status_code == 200


def test_time_routes_need_tickets_or_projects(admin_client, org_a):
    url = "/api/time-entries/timesheet/"
    for off in ("tickets", "projects"):
        set_modules_off(org_a, off)
        assert admin_client.get(url).status_code == 200, off
    set_modules_off(org_a, "tickets", "projects")
    response = admin_client.get(url)
    assert response.status_code == 404
    assert response.json() == module_off_body(org_a, "tickets")


def test_products_stay_while_deals_or_invoices_is_on(admin_client, org_a):
    url = "/api/invoices/products/"
    set_modules_off(org_a, "deals")
    assert admin_client.get(url).status_code == 200
    set_modules_off(org_a, "invoices")
    assert admin_client.get(url).status_code == 200
    response = admin_client.get("/api/invoices/")
    assert response.status_code == 404
    assert response.json() == module_off_body(org_a, "invoices")
    set_modules_off(org_a, "deals", "invoices")
    response = admin_client.get(url)
    assert response.status_code == 404
    assert response.json() == module_off_body(org_a, "invoices")


def test_on_modules_and_core_routes_answer_as_before(admin_client, org_a):
    set_modules_off(org_a, *KEYS)
    for url in (
        "/api/accounts/",
        "/api/contacts/",
        "/api/search/?q=ab",
        "/api/dashboard/today/",
        "/api/org/modules/",
    ):
        assert admin_client.get(url).status_code == 200, url


def test_the_refusal_uses_the_org_term(admin_client, org_a):
    org_a.terminology = {"lead.plural": "Enquiries"}
    org_a.save(update_fields=["terminology"])
    set_modules_off(org_a, "leads")
    response = admin_client.get("/api/leads/")
    assert response.status_code == 404
    assert (
        response.json()["detail"] == "Enquiries is switched off for this organization."
    )


def test_another_org_is_not_gated(admin_client, org_b_client, org_a):
    set_modules_off(org_a, "tickets")
    assert admin_client.get("/api/cases/").status_code == 404
    assert org_b_client.get("/api/cases/").status_code == 200
