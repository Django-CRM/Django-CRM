"""Linking a contact to a task needs the right to open that contact.

The task views linked contacts with only an org filter on create, PUT and
PATCH, after accounts, deals, tickets and leads had all moved to
`contacts.access.replace_visible_contacts`. Tasks nest their contacts in the
detail response, so a member could name any contact in the org on a task of
their own and read it back from there.

The rule is the helper's, unchanged: an id the caller cannot open is dropped
(the write still succeeds, as for another org's id), contacts already linked
that the caller cannot see stay linked on an edit, and an admin replaces
freely within the org.
"""

import pytest

from contacts.models import Contact
from tasks.models import Task

pytestmark = pytest.mark.django_db

URL = "/api/tasks/"
BODY = {"title": "Call back", "status": "New", "priority": "High"}


def _contact(org, created_by, first_name):
    contact = Contact.objects.create(
        first_name=first_name,
        last_name="Person",
        email=f"{first_name.lower()}@tasklink.test",
        org=org,
    )
    Contact.objects.filter(pk=contact.pk).update(created_by=created_by)
    return contact


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


def _names(task):
    return {c.first_name for c in task.contacts.all()}


@pytest.fixture
def task(org_a, regular_user, people):
    """A task the member created, linked to one contact they can open and one
    they cannot."""
    task = Task.objects.create(org=org_a, **BODY)
    # `BaseModel.save` nulls `created_by` outside a request.
    Task.objects.filter(pk=task.pk).update(created_by=regular_user)
    task.refresh_from_db()
    task.contacts.add(people["mine"], people["hidden"])
    return task


class TestCreate:
    def _post(self, client, people, *names):
        response = client.post(
            URL, {**BODY, "contacts": _ids(people, *names)}, format="json"
        )
        assert response.status_code == 200, response.content
        return Task.objects.order_by("-created_at").first()

    def test_member_cannot_link_a_contact_they_cannot_open(self, user_client, people):
        task = self._post(user_client, people, "mine", "hidden")
        assert _names(task) == {"Mine"}
        opened = user_client.get(f"/api/contacts/{people['hidden'].id}/")
        assert opened.status_code == 403

    def test_member_links_a_contact_they_can_open(self, user_client, people):
        task = self._post(user_client, people, "mine")
        assert _names(task) == {"Mine"}

    def test_admin_links_any_contact_but_never_another_orgs(self, admin_client, people):
        task = self._post(admin_client, people, "mine", "hidden", "foreign")
        assert _names(task) == {"Mine", "Hidden"}


class TestReplace:
    def _send(self, client, verb, task, people, *names):
        body = {"contacts": _ids(people, *names)}
        if verb == "put":
            body = {**BODY, **body}
        response = getattr(client, verb)(f"{URL}{task.id}/", body, format="json")
        assert response.status_code == 200, response.content
        return _names(task)

    @pytest.mark.parametrize("verb", ["put", "patch"])
    def test_member_edit_keeps_the_hidden_contact(
        self, user_client, task, people, verb
    ):
        assert self._send(user_client, verb, task, people, "mine") == {
            "Mine",
            "Hidden",
        }

    @pytest.mark.parametrize("verb", ["put", "patch"])
    def test_member_empty_list_unlinks_only_what_they_can_see(
        self, user_client, task, people, verb
    ):
        assert self._send(user_client, verb, task, people) == {"Hidden"}

    @pytest.mark.parametrize("verb", ["put", "patch"])
    def test_member_cannot_link_a_contact_they_cannot_open(
        self, user_client, task, people, verb
    ):
        linked = self._send(user_client, verb, task, people, "mine", "stranger")
        assert linked == {"Mine", "Hidden"}
        opened = user_client.get(f"/api/contacts/{people['stranger'].id}/")
        assert opened.status_code == 403

    @pytest.mark.parametrize("verb", ["put", "patch"])
    def test_member_links_a_contact_they_can_open(
        self, user_client, task, people, org_a, regular_user, verb
    ):
        people = {**people, "other": _contact(org_a, regular_user, "Other")}
        linked = self._send(user_client, verb, task, people, "mine", "other")
        assert linked == {"Mine", "Other", "Hidden"}

    @pytest.mark.parametrize("verb", ["put", "patch"])
    def test_admin_replaces_the_whole_list_within_the_org(
        self, admin_client, task, people, verb
    ):
        linked = self._send(admin_client, verb, task, people, "stranger", "foreign")
        assert linked == {"Stranger"}

    def test_patch_without_the_key_leaves_contacts_alone(
        self, user_client, task, people
    ):
        response = user_client.patch(f"{URL}{task.id}/", {}, format="json")
        assert response.status_code == 200, response.content
        assert _names(task) == {"Mine", "Hidden"}
