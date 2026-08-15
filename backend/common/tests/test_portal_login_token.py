"""The portal login token, and the RLS rule that governs where it may live."""

from datetime import timedelta

import pytest
from django.utils import timezone

from common.models import PortalLoginToken
from common.rls import ORG_SCOPED_TABLES
from contacts.models import Contact


@pytest.mark.django_db
def test_token_binds_one_contact_in_one_org(org_a):
    contact = Contact.objects.create(
        org=org_a, first_name="Pat", last_name="Smith", email="pat@example.com"
    )
    token = PortalLoginToken.objects.create(
        org=org_a,
        contact=contact,
        code_hash="not-a-real-hash",
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    assert token.is_used is False
    assert token.attempts == 0
    assert token.org == org_a
    assert token.contact == contact


def test_portal_login_token_is_not_org_scoped():
    """The lookup runs before any org context exists.

    An RLS policy here would hide the row from the only request that needs it,
    which is the same reason magic_link_token and portal_access_token are
    unscoped. Safe because the row is bound to one contact in one org when it
    is minted, and carries no tenant data of its own.
    """
    assert "portal_login_token" not in ORG_SCOPED_TABLES
