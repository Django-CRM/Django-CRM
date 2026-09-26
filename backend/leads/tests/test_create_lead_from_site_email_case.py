"""Web-to-lead links an existing contact whatever the case of the address.

`_attach_contact` used `get_or_create(email=email)`, an exact match. Contact
emails are unique per org case-insensitively (`Lower("email")`), so a
submission of "pat@example.com" against a stored "Pat@Example.com" missed the
existing row, tried to create a second one, hit the constraint, was logged,
and left the lead with no contact.
"""

import pytest

from common.models import APISettings
from contacts.models import Contact
from leads.models import Lead

URL = "/api/leads/create-from-site/"


@pytest.fixture
def api_setting(org_a, admin_user, admin_profile, user_profile):
    """A legacy key whose form, provisioned on first use, assigns to the member."""
    setting = APISettings.objects.create(
        title="Marketing site",
        website="https://example.com",
        org=org_a,
        created_by=admin_user,
    )
    setting.lead_assigned_to.add(user_profile)
    return setting


def payload(setting, **overrides):
    body = {
        "apikey": setting.apikey,
        "first_name": "Pat",
        "last_name": "Prospect",
        "email": "pat@example.com",
    }
    body.update(overrides)
    return body


@pytest.mark.django_db
@pytest.mark.parametrize(
    "stored, submitted",
    [
        ("Pat@Example.com", "pat@example.com"),
        ("pat@example.com", "PAT@EXAMPLE.COM"),
    ],
    ids=["stored-mixed", "submitted-upper"],
)
class TestEmailCaseInsensitiveMatch:
    def test_the_existing_contact_is_linked_and_not_duplicated(
        self, admin_client, org_a, admin_profile, api_setting, stored, submitted
    ):
        existing = Contact.objects.create(
            first_name="Known", last_name="Person", email=stored, org=org_a
        )

        response = admin_client.post(
            URL, payload(api_setting, email=submitted), format="json"
        )

        assert response.status_code == 200, response.data
        assert list(Lead.objects.get(org=org_a).contacts.all()) == [existing]
        assert Contact.objects.filter(org=org_a).count() == 1

    def test_the_existing_contacts_assignees_are_unchanged(
        self,
        admin_client,
        org_a,
        admin_profile,
        user_profile,
        api_setting,
        stored,
        submitted,
    ):
        existing = Contact.objects.create(
            first_name="Known", last_name="Person", email=stored, org=org_a
        )
        existing.assigned_to.add(admin_profile)

        admin_client.post(URL, payload(api_setting, email=submitted), format="json")

        assert set(existing.assigned_to.all()) == {admin_profile}
        existing.refresh_from_db()
        assert existing.email == stored


@pytest.mark.django_db
def test_an_address_in_another_org_is_not_matched(
    admin_client, org_a, org_b, api_setting
):
    """The match stays inside the key's org: another tenant's contact with the
    same address is neither linked nor blocks creating this org's own."""
    theirs = Contact.objects.create(
        first_name="Other", last_name="Tenant", email="Pat@Example.com", org=org_b
    )

    response = admin_client.post(URL, payload(api_setting), format="json")

    assert response.status_code == 200, response.data
    (contact,) = Lead.objects.get(org=org_a).contacts.all()
    assert contact != theirs
    assert contact.org_id == org_a.id
