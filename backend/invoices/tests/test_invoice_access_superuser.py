"""The invoice, estimate and recurring read rule admits a superuser through
the profile alone.

Since 1.12.0 `visible_invoices_qs`, `visible_estimates_qs` and
`visible_recurring_qs` take only the profile, and neither they nor
`has_object_access` carry an ``or ...is_superuser`` clause: `is_org_admin`
already admits a superuser's profile. These pin that dropping the clause did
not drop superuser access and that a plain member is still refused.
"""

from types import SimpleNamespace

import pytest
from django.utils import timezone

from common.models import Profile, User
from invoices.models import Estimate, Invoice, RecurringInvoice
from invoices.permissions import (
    has_object_access,
    visible_estimates_qs,
    visible_invoices_qs,
    visible_recurring_qs,
)


@pytest.fixture
def super_profile(org_a):
    user = User.objects.create_user(email="root@test.com", password="testpass123")
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])
    profile = Profile.objects.create(user=user, org=org_a, role="USER", is_active=True)
    # Loaded the way the middleware loads it, so the flag is read from the row.
    return Profile.objects.select_related("org", "user").get(pk=profile.pk)


@pytest.fixture
def documents(org_a):
    """One of each, created outside a request, so nobody owns any of them."""
    today = timezone.localdate()
    return (
        Invoice.objects.create(org=org_a, invoice_title="Work"),
        Estimate.objects.create(org=org_a, title="Quote"),
        RecurringInvoice.objects.create(
            org=org_a,
            title="Monthly",
            client_name="Client",
            client_email="client@example.com",
            frequency="MONTHLY",
            start_date=today,
            next_generation_date=today,
        ),
    )


def _visible(profile, documents):
    invoice, estimate, recurring = documents
    return (
        visible_invoices_qs(profile).filter(pk=invoice.pk).exists(),
        visible_estimates_qs(profile).filter(pk=estimate.pk).exists(),
        visible_recurring_qs(profile).filter(pk=recurring.pk).exists(),
    )


def test_superuser_with_a_user_role_profile_may_open_every_document(
    super_profile, documents
):
    assert super_profile.role == "USER"
    assert _visible(super_profile, documents) == (True, True, True)
    request = SimpleNamespace(profile=super_profile)
    assert has_object_access(request, documents[0]) is True
    assert has_object_access(request, documents[1]) is True


def test_plain_member_is_refused_documents_they_neither_made_nor_hold(
    user_profile, documents
):
    assert _visible(user_profile, documents) == (False, False, False)
    request = SimpleNamespace(profile=user_profile)
    assert has_object_access(request, documents[0]) is False
    assert has_object_access(request, documents[1]) is False
