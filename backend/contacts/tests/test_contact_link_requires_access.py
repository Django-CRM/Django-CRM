"""Linking a contact to a record needs the right to open that contact.

Every write path that takes a ``contacts`` id list used to accept any contact
in the org. A member could create an account, name any contact on it, assign
themselves, and then open that contact through the account: assignment to a
contact's account is one of the ways `has_contact_access` admits a reader.
Deals, tickets and leads nest their contacts in full, so linking one there
handed over a copy the same way.

All of them now go through `contacts.access.replace_visible_contacts`, the rule
account edits already followed: an id the caller cannot open is dropped (the
write still succeeds, as it does for an id from another org), and on a replace
the contacts already linked that the caller cannot see stay linked, because
the caller's form never held them.
"""

from unittest.mock import patch

import pytest

from accounts.models import Account
from cases.models import Case
from contacts.models import Contact
from leads.models import Lead
from opportunity.models import Opportunity

pytestmark = pytest.mark.django_db


def _contact(org, created_by, first_name):
    contact = Contact.objects.create(
        first_name=first_name,
        last_name="Person",
        email=f"{first_name.lower()}@link.test",
        org=org,
    )
    Contact.objects.filter(pk=contact.pk).update(created_by=created_by)
    return contact


def _stamp(obj, user):
    """Set `created_by` after the fact; `BaseModel.save` nulls it outside a
    request."""
    type(obj).objects.filter(pk=obj.pk).update(created_by=user)
    obj.refresh_from_db()
    return obj


@pytest.fixture
def people(org_a, org_b, admin_user, regular_user):
    return {
        "mine": _contact(org_a, regular_user, "Mine"),
        "hidden": _contact(org_a, admin_user, "Hidden"),
        "stranger": _contact(org_a, admin_user, "Stranger"),
        "foreign": _contact(org_b, None, "Foreign"),
    }


def _ids(people, *names):
    return [str(people[name].id) for name in names]


def _names(record):
    return {c.first_name for c in record.contacts.all()}


# One entry per kind of record: its URL, a body that creates or fully updates
# it, and a factory for a record the member created (so the member may edit it).
KINDS = {
    "deal": {
        "url": "/api/opportunities/",
        "body": {"name": "Deal", "stage": "QUALIFICATION"},
        "model": Opportunity,
        "make": lambda org: Opportunity.objects.create(
            name="Deal", stage="QUALIFICATION", org=org
        ),
    },
    "case": {
        "url": "/api/cases/",
        "body": {"name": "Ticket", "status": "New", "priority": "Normal"},
        "model": Case,
        "make": lambda org: Case.objects.create(
            name="Ticket", status="New", priority="Normal", org=org
        ),
    },
    "lead": {
        "url": "/api/leads/",
        "body": {"first_name": "Lee", "last_name": "Ad", "email": "lee@link.test"},
        "model": Lead,
        "make": lambda org: Lead.objects.create(
            first_name="Lee", last_name="Ad", email="lee@link.test", org=org
        ),
    },
}


def _created(kind):
    return KINDS[kind]["model"].objects.order_by("-created_at").first()


@pytest.fixture
def record(request, org_a, regular_user, people):
    """A record of the parametrized kind, created by the member and already
    linked to one contact they can open and one they cannot."""
    obj = _stamp(KINDS[request.param]["make"](org_a), regular_user)
    obj.contacts.add(people["mine"], people["hidden"])
    return request.param, obj


class TestAccountCreate:
    URL = "/api/accounts/"

    def _create(self, client, people, profile, *names):
        with patch("accounts.views.send_email_to_assigned_user.delay"):
            response = client.post(
                self.URL,
                {
                    "name": "Self Assigned Ltd",
                    "assigned_to": [str(profile.id)],
                    "contacts": _ids(people, *names),
                },
                format="json",
            )
        assert response.status_code == 200, response.content
        return Account.objects.get(id=response.json()["id"])

    def test_member_cannot_reach_a_contact_by_linking_it(
        self, user_client, user_profile, people
    ):
        account = self._create(user_client, people, user_profile, "mine", "hidden")
        assert _names(account) == {"Mine"}
        opened = user_client.get(f"/api/contacts/{people['hidden'].id}/")
        assert opened.status_code == 403

    def test_member_links_a_contact_they_can_open(
        self, user_client, user_profile, people
    ):
        account = self._create(user_client, people, user_profile, "mine")
        assert _names(account) == {"Mine"}

    def test_admin_links_any_contact_in_the_org(
        self, admin_client, admin_profile, people
    ):
        account = self._create(
            admin_client, people, admin_profile, "mine", "hidden", "foreign"
        )
        assert _names(account) == {"Mine", "Hidden"}


@pytest.mark.parametrize("kind", list(KINDS))
class TestCreate:
    def _post(self, client, kind, people, *names):
        body = {**KINDS[kind]["body"], "contacts": _ids(people, *names)}
        response = client.post(KINDS[kind]["url"], body, format="json")
        assert response.status_code == 200, response.content
        return _created(kind)

    def test_member_cannot_link_a_contact_they_cannot_open(
        self, user_client, people, kind
    ):
        obj = self._post(user_client, kind, people, "mine", "hidden")
        assert _names(obj) == {"Mine"}
        opened = user_client.get(f"/api/contacts/{people['hidden'].id}/")
        assert opened.status_code == 403

    def test_member_links_a_contact_they_can_open(self, user_client, people, kind):
        obj = self._post(user_client, kind, people, "mine")
        assert _names(obj) == {"Mine"}

    def test_admin_links_any_contact_but_never_another_orgs(
        self, admin_client, people, kind
    ):
        obj = self._post(admin_client, kind, people, "mine", "hidden", "foreign")
        assert _names(obj) == {"Mine", "Hidden"}


@pytest.mark.parametrize("record", list(KINDS), indirect=True)
class TestReplace:
    def _send(self, client, verb, record, people, *names):
        kind, obj = record
        body = {"contacts": _ids(people, *names)}
        if verb == "put":
            body = {**KINDS[kind]["body"], **body}
        response = getattr(client, verb)(
            f"{KINDS[kind]['url']}{obj.id}/", body, format="json"
        )
        assert response.status_code == 200, response.content
        return _names(obj)

    @pytest.mark.parametrize("verb", ["put", "patch"])
    def test_member_edit_keeps_the_hidden_contact(
        self, user_client, record, people, verb
    ):
        assert self._send(user_client, verb, record, people, "mine") == {
            "Mine",
            "Hidden",
        }

    @pytest.mark.parametrize("verb", ["put", "patch"])
    def test_member_empty_list_unlinks_only_what_they_can_see(
        self, user_client, record, people, verb
    ):
        assert self._send(user_client, verb, record, people) == {"Hidden"}

    @pytest.mark.parametrize("verb", ["put", "patch"])
    def test_member_cannot_link_a_contact_they_cannot_open(
        self, user_client, record, people, verb
    ):
        linked = self._send(user_client, verb, record, people, "mine", "stranger")
        assert linked == {"Mine", "Hidden"}
        opened = user_client.get(f"/api/contacts/{people['stranger'].id}/")
        assert opened.status_code == 403

    @pytest.mark.parametrize("verb", ["put", "patch"])
    def test_member_links_a_contact_they_can_open(
        self, user_client, record, people, org_a, regular_user, verb
    ):
        _contact(org_a, regular_user, "Other")
        people = {**people, "other": Contact.objects.get(first_name="Other")}
        linked = self._send(user_client, verb, record, people, "mine", "other")
        assert linked == {"Mine", "Other", "Hidden"}

    @pytest.mark.parametrize("verb", ["put", "patch"])
    def test_admin_replaces_the_whole_list_within_the_org(
        self, admin_client, record, people, verb
    ):
        linked = self._send(admin_client, verb, record, people, "stranger", "foreign")
        assert linked == {"Stranger"}

    def test_patch_without_the_key_leaves_contacts_alone(
        self, user_client, record, people
    ):
        kind, obj = record
        response = user_client.patch(
            f"{KINDS[kind]['url']}{obj.id}/", {}, format="json"
        )
        assert response.status_code == 200, response.content
        assert _names(obj) == {"Mine", "Hidden"}


class TestLeadConversion:
    """Conversion links contacts too: the lead's own, and the one it finds or
    creates by email. Both land on an account the converter is assigned to."""

    def _convert(self, client, lead):
        with (
            patch("leads.views.lead_views.send_email_to_assigned_user.delay"),
            patch("accounts.tasks.send_email_to_assigned_user.delay"),
        ):
            response = client.patch(
                f"/api/leads/{lead.id}/", {"status": "converted"}, format="json"
            )
        assert response.status_code == 200, response.content
        return response.json()

    @pytest.fixture
    def lead(self, org_a, regular_user, user_profile):
        lead = _stamp(
            Lead.objects.create(
                first_name="Lee",
                last_name="Ad",
                email="lee@link.test",
                company_name="Converted Co",
                org=org_a,
            ),
            regular_user,
        )
        lead.assigned_to.add(user_profile)
        return lead

    def test_a_hidden_contact_on_the_lead_is_not_carried_to_the_account(
        self, user_client, lead, people
    ):
        lead.contacts.add(people["mine"], people["hidden"])
        body = self._convert(user_client, lead)
        account = Account.objects.get(id=body["account_id"])
        assert "Hidden" not in _names(account)
        assert "Mine" in _names(account)
        opened = user_client.get(f"/api/contacts/{people['hidden'].id}/")
        assert opened.status_code == 403

    def test_a_hidden_contact_matched_by_email_is_not_linked(
        self, user_client, lead, people
    ):
        Lead.objects.filter(pk=lead.pk).update(email=people["hidden"].email)
        body = self._convert(user_client, lead)
        account = Account.objects.get(id=body["account_id"])
        hidden = Contact.objects.get(pk=people["hidden"].pk)
        assert body["contact_id"] is None
        assert _names(account) == set()
        assert hidden.account_id is None
        opened = user_client.get(f"/api/contacts/{hidden.id}/")
        assert opened.status_code == 403

    def test_a_visible_contact_matched_by_email_is_linked(
        self, user_client, lead, people
    ):
        Lead.objects.filter(pk=lead.pk).update(email=people["mine"].email)
        body = self._convert(user_client, lead)
        account = Account.objects.get(id=body["account_id"])
        assert body["contact_id"] == str(people["mine"].id)
        assert _names(account) == {"Mine"}

    def test_admin_conversion_links_the_matched_contact(
        self, admin_client, lead, people
    ):
        Lead.objects.filter(pk=lead.pk).update(email=people["hidden"].email)
        lead.contacts.add(people["stranger"])
        body = self._convert(admin_client, lead)
        account = Account.objects.get(id=body["account_id"])
        assert body["contact_id"] == str(people["hidden"].id)
        assert _names(account) == {"Hidden", "Stranger"}


class TestCaseImport:
    URL = "/api/cases/import/commit/"

    def _post(self, client, *emails):
        import csv
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["name", "status", "priority", "contact_emails"])
        writer.writerow(["Imported", "New", "Normal", ";".join(emails)])
        upload = SimpleUploadedFile(
            "t.csv", buf.getvalue().encode(), content_type="text/csv"
        )
        return client.post(self.URL, {"file": upload}, format="multipart")

    def _import(self, client, *emails):
        response = self._post(client, *emails)
        assert response.status_code == 200, response.content
        return Case.objects.get(id=response.json()["ids"][0])

    def test_member_import_naming_a_contact_they_cannot_open_is_refused(
        self, user_client, user_profile, people
    ):
        """A row error, the same one an unknown address gets, so the import
        neither links the contact nor confirms that it exists."""
        user_profile.has_sales_access = True
        user_profile.save(update_fields=["has_sales_access"])
        response = self._post(user_client, people["mine"].email, people["hidden"].email)
        assert response.status_code == 400, response.content
        assert [e["field"] for e in response.json()["errors"]] == ["contact_emails"]
        assert not Case.objects.exists()

    def test_member_import_links_a_contact_they_can_open(
        self, user_client, user_profile, people
    ):
        user_profile.has_sales_access = True
        user_profile.save(update_fields=["has_sales_access"])
        case = self._import(user_client, people["mine"].email)
        assert _names(case) == {"Mine"}

    def test_admin_import_links_any_contact(self, admin_client, people):
        case = self._import(admin_client, people["mine"].email, people["hidden"].email)
        assert _names(case) == {"Mine", "Hidden"}
