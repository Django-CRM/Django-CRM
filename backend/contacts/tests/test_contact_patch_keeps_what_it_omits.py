"""A contact edit from the phone keeps the relations its form does not show.

`ContactDetailView.put` clears `teams`, `assigned_to` and `tags`
unconditionally. The phone's contact form (`Contact.toPayload()`) sends none of
the three and edited with PUT, so every edit made from a phone unassigned the
contact and stripped its teams and tags. It now edits with PATCH, which touches
a relation only when its key is present.
"""

import pytest

from common.models import Tags, Teams
from contacts.models import Contact

pytestmark = pytest.mark.django_db


@pytest.fixture
def contact(org_a, admin_profile):
    contact = Contact.objects.create(
        first_name="Dana", last_name="Reed", email="dana@example.com", org=org_a
    )
    contact.teams.add(Teams.objects.create(name="Field", description="-", org=org_a))
    contact.tags.add(Tags.objects.create(name="VIP", org=org_a))
    contact.assigned_to.add(admin_profile)
    return contact


def _phone_body():
    """The keys `Contact.toPayload()` sends."""
    return {
        "first_name": "Dana",
        "last_name": "Reed-Khan",
        "email": "dana@example.com",
        "phone": None,
        "organization": None,
        "title": "Buyer",
        "department": None,
        "do_not_call": True,
        "linkedin_url": None,
        "address_line": None,
        "city": "Leeds",
        "state": None,
        "postcode": None,
        "country": None,
        "description": None,
        "account": None,
        "is_active": True,
    }


def test_patch_saves_the_form_and_keeps_every_relation(admin_client, contact):
    response = admin_client.patch(
        f"/api/contacts/{contact.id}/", _phone_body(), format="json"
    )
    assert response.status_code == 200, response.content
    contact.refresh_from_db()
    assert contact.last_name == "Reed-Khan"
    assert contact.title == "Buyer"
    assert contact.do_not_call is True
    assert contact.city == "Leeds"
    assert contact.teams.count() == 1
    assert contact.tags.count() == 1
    assert contact.assigned_to.count() == 1


def test_put_with_the_same_body_still_clears_them(admin_client, contact):
    """PUT stays a full replace, which is why the phone stopped using it."""
    response = admin_client.put(
        f"/api/contacts/{contact.id}/", _phone_body(), format="json"
    )
    assert response.status_code == 200, response.content
    assert contact.teams.count() == 0
    assert contact.tags.count() == 0
    assert contact.assigned_to.count() == 0
