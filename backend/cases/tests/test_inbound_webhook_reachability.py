"""The inbound email webhook works for the caller it exists for: AWS SNS.

SNS POSTs with no credential, so the request has no JWT and no org. The
webhook was dead in production twice over for that caller:

1. `RequireOrgContext` refused it with 403 "Organization context is required",
   because the path was not exempt. Every earlier webhook test signed in as an
   admin, which supplied the org claim SNS never has, and hid it.
2. Past that, the view read `inbound_mailbox` (org-scoped, FORCE RLS) before it
   set any context, so under the non-superuser production role the lookup saw
   nothing and answered 404.

The fix exempts this one route by URL name and resolves the org from the
unscoped `PortalAccessToken` lookup before the mailbox is read. Every test
here posts anonymously. The `postgres_only` tests at the foot prove the chain
under a role RLS actually binds; on SQLite and a superuser Postgres they would
pass vacuously, so they assert the empty context hides the mailbox first.
"""

from __future__ import annotations

import functools
import importlib
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.apps import apps as django_apps
from django.db import connection
from rest_framework.test import APIClient

from cases.inbound.sns import verify_sns_message
from cases.models import Case, InboundMailbox
from cases.tests.test_inbound_email import (
    SNS_TOPIC,
    _make_mailbox,
    _notification,
    _raw_email,
)
from cases.tests.test_inbound_sns import _make_cert, _pem, _signed_payload
from common.middleware.rls_context import RequireOrgContext
from common.models import Org, PortalAccessToken
from common.portal_tokens import (
    portal_token_hash,
    register_portal_token,
    resolve_portal_org,
)
from conftest import restore_rls_context, rls_org

MAILBOXES_URL = "/api/cases/mailboxes/"
ORG_CONTEXT_REQUIRED = "Organization context is required. Please login again."
NOT_FOUND = {"error": True, "errors": "Mailbox not found"}

backfill_migration = importlib.import_module(
    "common.migrations.0049_portal_token_inbound_mailbox"
)


def _webhook(mailbox_id):
    return f"/api/cases/inbound/{mailbox_id}/"


def _post(client, mailbox_id, payload):
    return client.post(_webhook(mailbox_id), payload, format="json")


def _lookup_rows(mailbox):
    return PortalAccessToken.objects.filter(
        resource_type=PortalAccessToken.INBOUND_MAILBOX, resource_id=mailbox.id
    )


@pytest.fixture
def anon():
    return APIClient()


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _real_verification(signing_key):
    """The production verifier with only the certificate download stubbed, so
    the signature over the payload is genuinely checked."""
    cert = _make_cert(signing_key)
    return patch(
        "cases.inbound_views.verify_sns_message",
        functools.partial(verify_sns_message, fetch_cert=lambda url: _pem(cert)),
    )


def _run_backfill(forward=True):
    schema_editor = SimpleNamespace(connection=connection)
    if forward:
        backfill_migration.register_mailboxes(django_apps, schema_editor)
    else:
        backfill_migration.unregister_mailboxes(django_apps, schema_editor)
    # The migration clears `app.current_org` on the way out, as it must.
    restore_rls_context()


# ---------------------------------------------------------------------------
# Reachable with no credential at all
# ---------------------------------------------------------------------------


class TestAnonymousDelivery:
    def test_signed_notification_creates_the_case(self, anon, org_a, signing_key):
        mailbox = _make_mailbox(org_a)
        payload = _signed_payload(
            signing_key, topic_arn=SNS_TOPIC, Message=_raw_email()
        )

        with _real_verification(signing_key):
            response = _post(anon, mailbox.id, payload)

        assert response.status_code == 200, response.content
        assert response.json()["created_case"] is True
        assert Case.objects.filter(org=org_a).count() == 1

    def test_a_tampered_notification_is_still_refused(self, anon, org_a, signing_key):
        """Reachability must not cost the signature check."""
        mailbox = _make_mailbox(org_a)
        payload = _signed_payload(
            signing_key, topic_arn=SNS_TOPIC, Message=_raw_email()
        )
        payload["Message"] = _raw_email(subject="Forged after signing")

        with _real_verification(signing_key):
            response = _post(anon, mailbox.id, payload)

        assert response.status_code == 403
        assert Case.objects.filter(org=org_a).count() == 0

    def test_subscription_confirmation_pins_the_mailbox(self, anon, org_a, settings):
        settings.INBOUND_SNS_ACCOUNT_IDS = frozenset({"123456789012"})
        mailbox = _make_mailbox(org_a, topic_arn="")
        with (
            patch("cases.inbound_views.verify_sns_message"),
            patch("cases.inbound_views.confirm_subscription") as confirm,
        ):
            response = _post(
                anon,
                mailbox.id,
                {
                    "Type": "SubscriptionConfirmation",
                    "SubscribeURL": "https://sns.us-east-1.amazonaws.com/x",
                    "TopicArn": SNS_TOPIC,
                },
            )

        assert response.status_code == 200, response.content
        assert confirm.called
        mailbox.refresh_from_db()
        assert mailbox.topic_arn == SNS_TOPIC

    @pytest.mark.parametrize(
        "form", [str.upper, lambda s: s.replace("-", ""), lambda s: "{%s}" % s]
    )
    def test_every_id_form_the_router_accepts_resolves(self, anon, org_a, form):
        """The `uid` converter canonicalises before the view hashes the id,
        so the lookup key matches however the id was written."""
        mailbox = _make_mailbox(org_a)
        with patch("cases.inbound_views.verify_sns_message"):
            response = _post(
                anon, form(str(mailbox.id)), _notification(SNS_TOPIC, _raw_email())
            )
        assert response.status_code == 200, response.content

    def test_unknown_inactive_and_deleted_ids_answer_the_same_404(self, anon, org_a):
        inactive = _make_mailbox(org_a, is_active=False)
        deleted = _make_mailbox(org_a, address="gone@acme.com")
        deleted_id = deleted.id
        deleted.delete()

        responses = [
            _post(anon, mailbox_id, _notification(SNS_TOPIC))
            for mailbox_id in (uuid.uuid4(), inactive.id, deleted_id)
        ]

        assert [r.status_code for r in responses] == [404, 404, 404]
        assert [r.json() for r in responses] == [NOT_FOUND] * 3

    def test_a_malformed_id_404s_without_reaching_the_view(self, anon):
        with patch("cases.inbound_views.resolve_portal_org") as resolve:
            response = _post(anon, "not-a-uuid", _notification(SNS_TOPIC))
        assert response.status_code == 404
        assert not resolve.called


# ---------------------------------------------------------------------------
# The exemption covers the webhook and nothing beside it
# ---------------------------------------------------------------------------


class TestExemptionIsOnlyTheWebhook:
    def test_mailbox_list_and_create_still_require_org_context(self, anon, org_a):
        for response in (anon.get(MAILBOXES_URL), anon.post(MAILBOXES_URL, {})):
            assert response.status_code == 403
            assert response.json() == {"detail": ORG_CONTEXT_REQUIRED}

    def test_mailbox_detail_still_requires_org_context(self, anon, org_a):
        url = f"{MAILBOXES_URL}{_make_mailbox(org_a).id}/"
        for response in (anon.get(url), anon.put(url, {}), anon.delete(url)):
            assert response.status_code == 403
            assert response.json() == {"detail": ORG_CONTEXT_REQUIRED}

    def test_the_exemption_is_by_name_not_by_path_prefix(self):
        middleware = RequireOrgContext(get_response=None)
        for path in (
            _webhook(uuid.uuid4()),
            "/api/cases/inbound/",
            MAILBOXES_URL,
            "/api/cases/",
        ):
            assert middleware._is_exempt(path) is False
        assert RequireOrgContext.EXEMPT_VIEW_NAMES == frozenset(
            {"common_urls:api_cases:inbound_webhook"}
        )

    def test_a_lookalike_path_under_the_webhook_is_not_served(self, anon, org_a):
        mailbox = _make_mailbox(org_a)
        response = anon.post(
            f"{_webhook(mailbox.id)}extra/", _notification(SNS_TOPIC), format="json"
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Org resolution never lands in the wrong tenant
# ---------------------------------------------------------------------------


class TestOrgResolution:
    def test_each_mailbox_resolves_to_its_own_org(self, anon, org_a, org_b):
        mine = _make_mailbox(org_a)
        theirs = _make_mailbox(org_b, address="help@beta.com")

        assert resolve_portal_org(str(mine.id), "inbound_mailbox") == str(org_a.id)
        assert resolve_portal_org(str(theirs.id), "inbound_mailbox") == str(org_b.id)

        with patch("cases.inbound_views.verify_sns_message"):
            response = _post(
                anon,
                theirs.id,
                _notification(SNS_TOPIC, _raw_email(to="help@beta.com")),
            )

        assert response.status_code == 200, response.content
        assert Case.objects.filter(org=org_a).count() == 0
        with rls_org(org_b):
            assert Case.objects.filter(org=org_b).count() == 1

    def test_a_lookup_row_naming_another_org_finds_no_mailbox(self, anon, org_a, org_b):
        """The scoped read re-checks the org, so a lookup row that disagrees
        with the mailbox (stale, or planted) cannot carry mail across."""
        theirs = _make_mailbox(org_b, address="help@beta.com")
        _lookup_rows(theirs).update(org=org_a)

        with patch("cases.inbound_views.verify_sns_message"):
            response = _post(anon, theirs.id, _notification(SNS_TOPIC, _raw_email()))

        assert response.status_code == 404
        assert response.json() == NOT_FOUND
        assert Case.objects.filter(org=org_a).count() == 0

    def test_a_token_of_another_resource_type_does_not_resolve(self, anon, org_a):
        mailbox = _make_mailbox(org_a)
        _lookup_rows(mailbox).update(resource_type=PortalAccessToken.INVOICE)

        with patch("cases.inbound_views.verify_sns_message"):
            response = _post(anon, mailbox.id, _notification(SNS_TOPIC, _raw_email()))

        assert response.status_code == 404
        assert response.json() == NOT_FOUND


# ---------------------------------------------------------------------------
# The lookup row follows the mailbox
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_creating_through_the_api_registers_the_mailbox(self, admin_client, org_a):
        response = admin_client.post(
            MAILBOXES_URL, {"address": "new@acme.com", "provider": "ses"}, format="json"
        )
        assert response.status_code == 201, response.content

        row = PortalAccessToken.objects.get(
            token_hash=portal_token_hash(response.json()["id"])
        )
        assert row.org_id == org_a.id
        assert row.resource_type == PortalAccessToken.INBOUND_MAILBOX
        assert str(row.resource_id) == response.json()["id"]

    def test_deleting_through_the_api_removes_the_row(self, admin_client, org_a):
        mailbox = _make_mailbox(org_a)
        assert _lookup_rows(mailbox).count() == 1

        response = admin_client.delete(f"{MAILBOXES_URL}{mailbox.id}/")

        assert response.status_code == 200
        assert _lookup_rows(mailbox).count() == 0

    def test_an_update_neither_duplicates_nor_drops_the_row(self, admin_client, org_a):
        mailbox = _make_mailbox(org_a)
        response = admin_client.put(
            f"{MAILBOXES_URL}{mailbox.id}/", {"is_active": False}, format="json"
        )
        assert response.status_code == 200
        assert _lookup_rows(mailbox).count() == 1

    def test_deactivating_keeps_the_row_and_the_webhook_404s_until_reactivated(
        self, admin_client, anon, org_a
    ):
        mailbox = _make_mailbox(org_a)
        url = f"{MAILBOXES_URL}{mailbox.id}/"

        admin_client.put(url, {"is_active": False}, format="json")
        with patch("cases.inbound_views.verify_sns_message"):
            off = _post(anon, mailbox.id, _notification(SNS_TOPIC, _raw_email()))
        assert off.status_code == 404
        assert off.json() == NOT_FOUND

        admin_client.put(url, {"is_active": True}, format="json")
        with patch("cases.inbound_views.verify_sns_message"):
            on = _post(anon, mailbox.id, _notification(SNS_TOPIC, _raw_email()))
        assert on.status_code == 200, on.content


# ---------------------------------------------------------------------------
# Backfill (common/0049)
# ---------------------------------------------------------------------------


class TestBackfill:
    def test_forward_registers_every_mailbox_under_its_own_org(self, org_a, org_b):
        mine = _make_mailbox(org_a)
        paused = _make_mailbox(org_a, address="paused@acme.com", is_active=False)
        theirs = _make_mailbox(org_b, address="help@beta.com")
        # As the rows stood before this release: nothing registered.
        PortalAccessToken.objects.filter(resource_type="inbound_mailbox").delete()

        _run_backfill()

        for mailbox, org in ((mine, org_a), (paused, org_a), (theirs, org_b)):
            row = PortalAccessToken.objects.get(
                token_hash=portal_token_hash(str(mailbox.id))
            )
            assert (row.org_id, row.resource_type, row.resource_id) == (
                org.id,
                "inbound_mailbox",
                mailbox.id,
            )

    def test_forward_is_idempotent(self, org_a):
        mailbox = _make_mailbox(org_a)
        _run_backfill()
        _run_backfill()
        assert _lookup_rows(mailbox).count() == 1

    def test_reverse_removes_only_the_mailbox_rows(self, org_a):
        mailbox = _make_mailbox(org_a)
        register_portal_token("an-invoice-token", org_a.id, "invoice", uuid.uuid4())

        _run_backfill(forward=False)

        assert _lookup_rows(mailbox).count() == 0
        assert PortalAccessToken.objects.filter(resource_type="invoice").count() == 1


# ---------------------------------------------------------------------------
# Under a role RLS binds (RLS_ENFORCE run, --ds=crm.test_settings_postgres)
# ---------------------------------------------------------------------------


def _context(value):
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_org', %s, false)", [value])


def _enforced_mailbox():
    """An org and an active pinned mailbox, then an empty context, asserting
    that the empty context really hides the mailbox (else the run is vacuous)."""
    org = Org.objects.create(name="RLS Inbound Org")
    _context(str(org.id))
    try:
        mailbox = InboundMailbox.objects.create(
            org=org, address="support@rls.test", provider="ses", topic_arn=SNS_TOPIC
        )
    finally:
        _context("")
    assert not InboundMailbox.objects.filter(pk=mailbox.pk).exists(), (
        "The empty context exposed inbound_mailbox: this role bypasses RLS."
    )
    return org, mailbox


@pytest.mark.postgres_only
def test_webhook_delivers_under_enforced_rls(anon):
    if connection.vendor != "postgresql":
        pytest.skip("RLS requires PostgreSQL")
    org, mailbox = _enforced_mailbox()

    with patch("cases.inbound_views.verify_sns_message"):
        response = _post(
            anon,
            mailbox.id,
            _notification(SNS_TOPIC, _raw_email(to=mailbox.address)),
        )

    assert response.status_code == 200, response.content
    assert response.json()["created_case"] is True
    _context(str(org.id))
    try:
        assert Case.objects.filter(org=org).count() == 1
    finally:
        _context("")


@pytest.mark.postgres_only
def test_backfill_finds_mailboxes_the_empty_context_hides():
    if connection.vendor != "postgresql":
        pytest.skip("RLS requires PostgreSQL")
    org, mailbox = _enforced_mailbox()
    _lookup_rows(mailbox).delete()

    _run_backfill()

    assert resolve_portal_org(str(mailbox.id), "inbound_mailbox") == str(org.id)


@pytest.mark.postgres_only
def test_admin_mailbox_endpoints_refuse_without_org_context(anon):
    if connection.vendor != "postgresql":
        pytest.skip("RLS requires PostgreSQL")
    _org, mailbox = _enforced_mailbox()
    for response in (
        anon.get(MAILBOXES_URL),
        anon.get(f"{MAILBOXES_URL}{mailbox.id}/"),
    ):
        assert response.status_code == 403
        assert response.json() == {"detail": ORG_CONTEXT_REQUIRED}
