"""An app member writes records, never configuration.

It is an admin for reading and for what an admin may do to records, so a
connector sees and edits the org's records. Every admin-only write that changes
how the org is set up refuses it (`common.permissions.may_configure`), so a
leaked app key cannot reconfigure the org. Each configuration below is read by
the app member, refused to it on write, and still writable by a person admin.
"""

import pytest
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Account
from common.app_members import create_app_member
from common.models import PersonalAccessToken
from common.permissions import IsOrgAdmin, may_admin, may_configure
from contacts.models import Contact
from leads.models import Lead
from tasks.models import Task

RECORD_ROOTS = (
    "leads",
    "contacts",
    "accounts",
    "opportunities",
    "tasks",
    "cases",
    "invoices",
    "interactions",
)


@pytest.fixture
def app_member(org_a, admin_profile):
    return create_app_member(org_a, "Sync app")


def _client(profile, scopes):
    raw, _ = PersonalAccessToken.generate(profile=profile, name="k", scopes=scopes)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


@pytest.fixture
def app_client(app_member):
    """A key with read and write on every record root, as a connector holds."""
    held = [f"{root}:{action}" for root in RECORD_ROOTS for action in ("read", "write")]
    return _client(app_member, held)


@pytest.fixture
def unrestricted_app_client(app_member):
    return _client(app_member, [])


def test_the_predicates(admin_profile, app_member):
    assert may_configure(admin_profile) is True
    assert may_configure(app_member) is False
    factory = RequestFactory()
    for method, allowed in (("get", True), ("post", False), ("delete", False)):
        request = getattr(factory, method)("/api/x/")
        request.profile = app_member
        assert may_admin(request) is allowed
        assert IsOrgAdmin().has_permission(request, None) is allowed
        request.profile = admin_profile
        assert may_admin(request) is True


# (root, write method, write path, read path)
CONFIGURATION = [
    ("leads", "post", "/api/leads/pipelines/", "/api/leads/pipelines/"),
    ("leads", "post", "/api/leads/sources/", "/api/leads/sources/"),
    ("leads", "post", "/api/leads/intakes/", None),
    ("opportunities", "post", "/api/opportunities/pipelines/", None),
    ("opportunities", "put", "/api/opportunities/aging-config/", None),
    ("opportunities", "post", "/api/opportunities/goals/", None),
    ("tasks", "post", "/api/tasks/pipelines/", "/api/tasks/pipelines/"),
    ("cases", "post", "/api/cases/pipelines/", "/api/cases/pipelines/"),
    ("cases", "post", "/api/cases/mailboxes/", "/api/cases/mailboxes/"),
    ("cases", "post", "/api/cases/approval-rules/", "/api/cases/approval-rules/"),
    ("cases", "post", "/api/cases/escalation-policies/", None),
    ("cases", "post", "/api/cases/routing-rules/", "/api/cases/routing-rules/"),
    ("cases", "put", "/api/cases/reopen-policy/", "/api/cases/reopen-policy/"),
    ("invoices", "post", "/api/invoices/products/", "/api/invoices/products/"),
    ("invoices", "post", "/api/invoices/templates/", "/api/invoices/templates/"),
]


@pytest.mark.parametrize(
    "root,method,path,read",
    CONFIGURATION,
    ids=[
        f"{method}-{path[5:-1].replace('/', '-')}"
        for _, method, path, _ in CONFIGURATION
    ],
)
def test_app_member_reads_but_cannot_change_configuration(
    root, method, path, read, app_client, admin_client
):
    refused = getattr(app_client, method)(path, {}, format="json")
    assert refused.status_code == 403, refused.content
    assert getattr(admin_client, method)(path, {}, format="json").status_code != 403
    read = read or path
    assert app_client.get(read).status_code == 200


# Roots no connector is offered, reached by a key with no scopes at all.
UNSCOPED = [
    ("post", "/api/teams/"),
    ("post", "/api/tags/"),
    ("post", "/api/custom-fields/"),
    ("patch", "/api/org/settings/"),
    ("put", "/api/org/modules/"),
    ("post", "/api/api-settings/"),
    ("put", "/api/business-hours/calendar/"),
    ("post", "/api/webforms/"),
    ("post", "/api/users/"),
    ("post", "/api/org/"),
]


@pytest.mark.parametrize("method,path", UNSCOPED, ids=[p for _, p in UNSCOPED])
def test_an_unrestricted_app_key_cannot_change_configuration(
    method, path, unrestricted_app_client, admin_client
):
    refused = getattr(unrestricted_app_client, method)(
        path, {"name": "x"}, format="json"
    )
    assert refused.status_code == 403, refused.content
    if path != "/api/org/":
        allowed = getattr(admin_client, method)(path, {"name": "x"}, format="json")
        assert allowed.status_code != 403


def _lead(org):
    return Lead.objects.create(org=org, first_name="Pat")


def _invoice_body(org):
    account = Account.objects.create(org=org, name="Acme")
    contact = Contact.objects.create(org=org, first_name="Ada", last_name="B")
    return {
        "account_id": str(account.id),
        "contact_id": str(contact.id),
        "currency": "USD",
        "issue_date": str(timezone.localdate()),
        "invoice_title": "Rebuild",
        "line_items": [{"name": "Design", "quantity": "1", "unit_price": "10.00"}],
    }


# root: (create body, update verb)
RECORDS = {
    "leads": (lambda org: {"first_name": "New", "email": "n@example.com"}, "put"),
    "contacts": (
        lambda org: {"first_name": "New", "last_name": "C", "email": "c@example.com"},
        "put",
    ),
    "accounts": (lambda org: {"name": "New account"}, "put"),
    "opportunities": (lambda org: {"name": "Deal", "stage": "QUALIFICATION"}, "put"),
    "tasks": (
        lambda org: {"title": "Call", "status": "New", "priority": "Low"},
        "put",
    ),
    "cases": (
        lambda org: {"name": "Case", "status": "New", "priority": "Normal"},
        "put",
    ),
    "invoices": (_invoice_body, "put"),
    "interactions": (lambda org: {"kind": "call", "lead": str(_lead(org).id)}, "patch"),
}


@pytest.mark.parametrize("root", RECORD_ROOTS)
def test_app_member_still_creates_and_updates_records(root, app_client, org_a):
    make, verb = RECORDS[root]
    body = make(org_a)
    created = app_client.post(f"/api/{root}/", body, format="json")
    assert created.status_code in (200, 201), created.content
    reply = created.json()
    pk = reply.get("id") or (reply.get("invoice") or {}).get("id")
    if root == "tasks":
        # The task create reply carries no id.
        pk = Task.objects.get(org=org_a, title="Call").id
    assert pk, reply
    update = {**body, "description": "Updated by the app"}
    if root == "interactions":
        update = {"body": "Updated by the app"}
    changed = getattr(app_client, verb)(f"/api/{root}/{pk}/", update, format="json")
    assert changed.status_code == 200, changed.content
