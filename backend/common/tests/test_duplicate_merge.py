"""G19: the duplicate endpoints and merging, for leads, contacts and accounts.

Who may merge (owner decision): both records readable, the module's write rule
on the keeper and its delete rule on the loser. A record the caller cannot
read answers the same 404 as a missing id, on either side of the merge.
"""

import uuid
from datetime import date, timedelta
from unittest import mock

import pytest
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from accounts.models import Account, AccountEmail, AccountEmailLog
from cases.models import Case, CsatSurvey
from common.audit_log import SecurityAuditLog
from common.models import (
    Activity,
    Attachments,
    Comment,
    Notification,
    PortalLoginToken,
    Profile,
    Tags,
    User,
)
from common.portal_auth import mint_portal_token
from contacts.models import Contact
from invoices.models import Estimate, Invoice, RecurringInvoice
from leads.models import Lead
from opportunity.models import Opportunity
from orders.models import Order
from tasks.models import Board, BoardColumn, BoardTask, Task
from webforms.models import WebForm, WebFormSubmission
from webhooks.models import WebhookDelivery, WebhookEndpoint

HIT_KEYS = {"id", "name", "email", "phone", "matched_on", "can_delete"}


@pytest.fixture
def other_user(org_a):
    """A second non-admin in org A, to own records `user_profile` cannot see."""
    user = User.objects.create_user(email="other@test.com", password="x")
    Profile.objects.create(user=user, org=org_a, role="USER", is_active=True)
    return user


def _contact(org, first="Ann", last="Lee", **fields):
    return Contact.objects.create(org=org, first_name=first, last_name=last, **fields)


def _merge(client, module, keeper, loser_id):
    return client.post(
        f"/api/{module}/{keeper}/merge/", {"merge_id": str(loser_id)}, format="json"
    )


# ---------------------------------------------------------------------------
# Relation census
# ---------------------------------------------------------------------------

# Every relation that points at the three models, and what a merge does with
# it. `merge_records` finds these from `_meta` and moves all but the portal
# codes, so a new one would be moved too; this test fails so that somebody
# decides whether moving is right (a OneToOne, for one, cannot be moved
# without an IntegrityError, and a credential must not be).
EXPECTED_RELATIONS = {
    Lead: {
        "tasks.Task.lead",
        "webforms.WebFormSubmission.lead",
    },
    Contact: {
        "common.Comment.commented_by_contact",
        "common.PortalLoginToken.contact",  # dies with the loser, on purpose
        "accounts.Account.contacts",
        "accounts.AccountEmail.recipients",
        "accounts.AccountEmailLog.contact",
        "cases.Case.contacts",
        "cases.CsatSurvey.contact",
        "leads.Lead.contacts",
        "opportunity.Opportunity.contacts",
        "tasks.BoardTask.contact",
        "tasks.Task.contacts",
        "invoices.Invoice.contact",
        "invoices.Estimate.contact",
        "invoices.RecurringInvoice.contact",
        "orders.Order.contact",
    },
    Account: {
        "accounts.AccountEmail.from_account",
        "cases.Case.account",
        "contacts.Contact.account",
        "opportunity.Opportunity.account",
        "tasks.BoardTask.account",
        "tasks.Task.account",
        "invoices.Invoice.account",
        "invoices.Estimate.account",
        "invoices.RecurringInvoice.account",
        "orders.Order.account",
    },
}

EXPECTED_OWN_M2M = {
    Lead: {"assigned_to", "teams", "tags", "contacts"},
    Contact: {"assigned_to", "teams", "tags"},
    Account: {"assigned_to", "teams", "contacts", "tags"},
}

# Tables that point at a record by (type, id) instead of a foreign key. The
# merge moves every (content_type, object_id) table but the admin log, which is
# the admin's own history, and moves activity; a notification is a message
# already delivered about the loser and stays as sent.
EXPECTED_ID_KEYED = {
    "admin.LogEntry",
    "common.Comment",
    "common.Attachments",
    "common.Activity",
    "common.Notification",
}


@pytest.mark.parametrize("model", [Lead, Contact, Account])
def test_every_relation_pointing_at_the_model_is_accounted_for(model):
    live = {
        f"{rel.related_model._meta.label}.{rel.field.name}"
        for rel in model._meta.related_objects
    }
    assert live == EXPECTED_RELATIONS[model]
    assert all(
        rel.one_to_many or rel.many_to_many for rel in model._meta.related_objects
    )
    assert {f.name for f in model._meta.many_to_many} == EXPECTED_OWN_M2M[model]


def test_the_merge_moves_every_generic_table_but_the_admin_log():
    from common.duplicate_detection import _generic_keyed_models

    assert {m._meta.label for m in _generic_keyed_models()} == {
        "common.Comment",
        "common.Attachments",
    }


def test_every_id_keyed_table_is_accounted_for():
    from django.apps import apps

    pairs = (("content_type", "object_id"), ("entity_type", "entity_id"))
    live = set()
    for model in apps.get_models():
        names = {f.name for f in model._meta.concrete_fields}
        if any(a in names and b in names for a, b in pairs):
            live.add(model._meta.label)
    assert live == EXPECTED_ID_KEYED


# ---------------------------------------------------------------------------
# Detection endpoints
# ---------------------------------------------------------------------------


def _check(client, module, **criteria):
    return client.post(f"/api/{module}/duplicates/", criteria, format="json")


class TestDuplicateCheck:
    def test_returns_list_level_fields_only(self, admin_client, org_a):
        _contact(org_a, email="ann@x.com", phone="202 555 0147", description="secret")
        response = _check(admin_client, "contacts", email="ANN@x.com")
        assert response.status_code == 200
        [hit] = response.json()["duplicates"]
        assert set(hit) == HIT_KEYS
        assert hit["matched_on"] == ["email"]
        assert hit["can_delete"] is True

    def test_the_criteria_never_travel_in_the_url(self, admin_client, org_a):
        """An email and a phone in a query string are written to every access
        log between the client and the API. The check takes a body."""
        _contact(org_a, email="ann@x.com")
        get = admin_client.get("/api/contacts/duplicates/?email=ann@x.com")
        assert get.status_code == 405

    def test_it_writes_nothing(self, admin_client, org_a):
        _contact(org_a, email="ann@x.com")
        _check(admin_client, "contacts", email="ann@x.com", first_name="New")
        assert Contact.objects.filter(org=org_a).count() == 1

    def test_a_hidden_record_is_neither_shown_nor_counted(
        self, user_client, org_a, other_user
    ):
        _contact(org_a, email="ann@x.com", created_by=other_user)
        response = _check(user_client, "contacts", email="ann@x.com")
        assert response.status_code == 200
        assert response.json() == {"duplicates": []}

    def test_another_orgs_record_is_never_found(self, admin_client, org_b):
        Lead.objects.create(org=org_b, email="theirs@x.com")
        response = _check(admin_client, "leads", email="theirs@x.com")
        assert response.json() == {"duplicates": []}

    def test_an_overlong_value_is_a_400(self, admin_client):
        response = _check(admin_client, "accounts", name="a" * 256)
        assert response.status_code == 400

    def test_no_criteria_finds_nothing(self, admin_client, org_a):
        Account.objects.create(org=org_a, name="Acme")
        response = _check(admin_client, "accounts")
        assert response.json() == {"duplicates": []}

    def test_anonymous_is_refused(self, unauthenticated_client):
        response = _check(unauthenticated_client, "leads", email="a@x.com")
        assert response.status_code in (401, 403)

    def test_can_delete_is_false_for_a_record_the_caller_did_not_create(
        self, user_client, org_a, other_user, user_profile
    ):
        theirs = Account.objects.create(org=org_a, name="Acme", created_by=other_user)
        theirs.assigned_to.add(user_profile)
        [hit] = _check(user_client, "accounts", name="acme").json()["duplicates"]
        assert hit["can_delete"] is False


class TestRecordDuplicates:
    def test_lists_others_but_not_itself(self, admin_client, org_a):
        one = Lead.objects.create(org=org_a, first_name="Bo", last_name="Li")
        two = Lead.objects.create(org=org_a, first_name="bo", last_name="LI")
        response = admin_client.get(f"/api/leads/{one.id}/duplicates/")
        assert response.status_code == 200
        body = response.json()
        assert body["can_delete"] is True
        assert [d["id"] for d in body["duplicates"]] == [str(two.id)]

    def test_hidden_missing_and_foreign_records_are_the_same_404(
        self, user_client, org_a, org_b, other_user
    ):
        hidden = _contact(org_a, created_by=other_user)
        foreign = _contact(org_b)
        bodies = []
        for pk in (hidden.id, foreign.id, uuid.uuid4()):
            response = user_client.get(f"/api/contacts/{pk}/duplicates/")
            assert response.status_code == 404
            bodies.append(response.json())
        assert bodies[0] == bodies[1] == bodies[2]

    def test_a_converted_lead_offers_nothing_to_merge(self, admin_client, org_a):
        done = Lead.objects.create(
            org=org_a, email="d@x.com", status="converted", first_name="Di"
        )
        Lead.objects.create(org=org_a, first_name="Di", phone="555 123 4567")
        response = admin_client.get(f"/api/leads/{done.id}/duplicates/")
        assert response.json()["duplicates"] == []

    def test_a_malformed_id_is_a_404(self, admin_client):
        assert (
            admin_client.get("/api/accounts/not-a-uuid/duplicates/").status_code == 404
        )


# ---------------------------------------------------------------------------
# Merge: who may
# ---------------------------------------------------------------------------


class TestMergeAuthorization:
    def test_admin_may_merge(self, admin_client, org_a):
        keeper, loser = _contact(org_a), _contact(org_a)
        response = _merge(admin_client, "contacts", keeper.id, loser.id)
        assert response.status_code == 200, response.content
        assert response.json()["id"] == str(keeper.id)
        assert not Contact.objects.filter(id=loser.id).exists()

    def test_a_writer_who_may_not_delete_the_loser_is_refused(
        self, user_client, org_a, other_user, user_profile
    ):
        # Assigned to both, so both are readable and writable; created by
        # somebody else, so neither is theirs to delete.
        keeper = _contact(org_a, created_by=other_user)
        loser = _contact(org_a, created_by=other_user, email="l@x.com")
        keeper.assigned_to.add(user_profile)
        loser.assigned_to.add(user_profile)
        response = _merge(user_client, "contacts", keeper.id, loser.id)
        assert response.status_code == 403
        assert Contact.objects.filter(id=loser.id).exists()
        keeper.refresh_from_db()
        assert keeper.email is None

    def test_the_creator_of_the_loser_may_merge_it_into_one_they_can_edit(
        self, user_client, org_a, other_user, user_profile
    ):
        keeper = Lead.objects.create(org=org_a, first_name="K", created_by=other_user)
        keeper.assigned_to.add(user_profile)
        loser = Lead.objects.create(
            org=org_a, first_name="L", created_by=user_profile.user
        )
        response = _merge(user_client, "leads", keeper.id, loser.id)
        assert response.status_code == 200, response.content
        assert not Lead.objects.filter(id=loser.id).exists()

    @pytest.mark.parametrize("side", ["keeper", "loser"])
    def test_a_hidden_record_on_either_side_is_the_missing_404(
        self, user_client, org_a, other_user, user_profile, side
    ):
        mine = Account.objects.create(
            org=org_a, name="Mine", created_by=user_profile.user
        )
        hidden = Account.objects.create(org=org_a, name="Hidden", created_by=other_user)
        if side == "keeper":
            hidden_response = _merge(user_client, "accounts", hidden.id, mine.id)
            missing_response = _merge(user_client, "accounts", uuid.uuid4(), mine.id)
        else:
            hidden_response = _merge(user_client, "accounts", mine.id, hidden.id)
            missing_response = _merge(user_client, "accounts", mine.id, uuid.uuid4())
        assert hidden_response.status_code == missing_response.status_code == 404
        assert hidden_response.json() == missing_response.json()
        assert Account.objects.filter(id=hidden.id).exists()
        assert Account.objects.filter(id=mine.id).exists()

    def test_the_404_body_matches_the_detail_views(self, admin_client, org_a):
        keeper = _contact(org_a)
        missing = uuid.uuid4()
        merge = _merge(admin_client, "contacts", keeper.id, missing)
        detail = admin_client.get(f"/api/contacts/{missing}/")
        assert merge.json() == detail.json()

    def test_another_orgs_record_cannot_be_merged_either_way(
        self, admin_client, org_a, org_b
    ):
        mine, theirs = _contact(org_a), _contact(org_b, email="t@x.com")
        assert _merge(admin_client, "contacts", mine.id, theirs.id).status_code == 404
        assert _merge(admin_client, "contacts", theirs.id, mine.id).status_code == 404
        assert Contact.objects.filter(id=theirs.id, org=org_b).exists()
        mine.refresh_from_db()
        assert mine.email is None

    def test_a_record_cannot_be_merged_into_itself(self, admin_client, org_a):
        one = _contact(org_a)
        response = _merge(admin_client, "contacts", one.id, one.id)
        assert response.status_code == 400
        assert Contact.objects.filter(id=one.id).exists()

    @pytest.mark.parametrize("body", [{}, {"merge_id": "nope"}, {"merge_id": ""}])
    def test_a_missing_or_malformed_merge_id_is_a_400(self, admin_client, org_a, body):
        one = _contact(org_a)
        response = admin_client.post(
            f"/api/contacts/{one.id}/merge/", body, format="json"
        )
        assert response.status_code == 400

    def test_a_malformed_keeper_id_is_a_404(self, admin_client, org_a):
        loser = _contact(org_a)
        assert _merge(admin_client, "contacts", "nope", loser.id).status_code == 404

    @pytest.mark.parametrize("converted", ["keeper", "loser"])
    def test_a_converted_lead_cannot_be_merged(self, admin_client, org_a, converted):
        done = Lead.objects.create(org=org_a, email="d@x.com", status="converted")
        open_lead = Lead.objects.create(org=org_a, first_name="Open")
        keeper, loser = (
            (done, open_lead) if converted == "keeper" else (open_lead, done)
        )
        response = _merge(admin_client, "leads", keeper.id, loser.id)
        assert response.status_code == 400
        assert Lead.objects.filter(id=loser.id).exists()

    def test_anonymous_is_refused(self, unauthenticated_client, org_a):
        one, two = _contact(org_a), _contact(org_a)
        response = _merge(unauthenticated_client, "contacts", one.id, two.id)
        assert response.status_code in (401, 403)
        assert Contact.objects.filter(id=two.id).exists()


# ---------------------------------------------------------------------------
# Merge: what moves
# ---------------------------------------------------------------------------


class TestContactMerge:
    def test_every_link_moves_to_the_keeper(self, admin_client, org_a, admin_user):
        keeper, loser = _contact(org_a, "Kay"), _contact(org_a, "Lou")
        account = Account.objects.create(org=org_a, name="Acme")
        account.contacts.add(loser)
        mail = AccountEmail.objects.create(
            org=org_a, from_account=account, from_email="a@x.com"
        )
        mail.recipients.add(loser)
        log = AccountEmailLog.objects.create(org=org_a, email=mail, contact=loser)
        case = Case.objects.create(org=org_a, name="C", status="New", priority="Low")
        case.contacts.add(loser)
        survey = CsatSurvey.objects.create(
            org=org_a,
            case=case,
            contact=loser,
            token_hash="h" * 64,
            sent_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=30),
        )
        lead = Lead.objects.create(org=org_a, first_name="L")
        lead.contacts.add(loser)
        deal = Opportunity.objects.create(org=org_a, name="Deal")
        deal.contacts.add(loser, keeper)  # already on both: must not double up
        board = Board.objects.create(
            name="B", org=org_a, owner=Profile.objects.get(user=admin_user)
        )
        column = BoardColumn.objects.create(board=board, name="Col", order=1, org=org_a)
        card = BoardTask.objects.create(
            column=column, title="Card", org=org_a, contact=loser
        )
        task = Task.objects.create(org=org_a, title="T", status="New", priority="Low")
        task.contacts.add(loser)
        invoice = Invoice.objects.create(
            org=org_a, invoice_title="I", invoice_number="1", contact=loser
        )
        estimate = Estimate.objects.create(org=org_a, title="E", contact=loser)
        recurring = RecurringInvoice.objects.create(
            org=org_a,
            title="R",
            frequency="MONTHLY",
            start_date=date.today(),
            next_generation_date=date.today(),
            contact=loser,
        )
        order = Order.objects.create(
            org=org_a, name="O", account=account, contact=loser
        )
        ct = ContentType.objects.get_for_model(Contact)
        note = Comment.objects.create(
            org=org_a, content_type=ct, object_id=loser.id, comment="hi"
        )
        reply = Comment.objects.create(
            org=org_a,
            content_type=ContentType.objects.get_for_model(Case),
            object_id=case.id,
            comment="from the portal",
            commented_by_contact=loser,
        )
        file = Attachments.objects.create(
            org=org_a,
            content_type=ct,
            object_id=loser.id,
            file_name="x.txt",
            attachment="attachments/x.txt",
        )
        activity = Activity.objects.create(
            org=org_a, action="UPDATE", entity_type="Contact", entity_id=loser.id
        )
        notice = Notification.objects.create(
            org=org_a,
            recipient=Profile.objects.get(user=admin_user),
            verb="assigned",
            entity_type="Contact",
            entity_id=loser.id,
        )

        response = _merge(admin_client, "contacts", keeper.id, loser.id)
        assert response.status_code == 200, response.content

        assert list(account.contacts.all()) == [keeper]
        assert list(mail.recipients.all()) == [keeper]
        log.refresh_from_db()
        assert log.contact_id == keeper.id
        assert list(case.contacts.all()) == [keeper]
        survey.refresh_from_db()
        assert survey.contact_id == keeper.id
        assert list(lead.contacts.all()) == [keeper]
        assert list(deal.contacts.all()) == [keeper]
        card.refresh_from_db()
        assert card.contact_id == keeper.id
        assert list(task.contacts.all()) == [keeper]
        for doc in (invoice, estimate, recurring, order):
            doc.refresh_from_db()
            assert doc.contact_id == keeper.id
        for row in (note, file):
            row.refresh_from_db()
            assert row.object_id == keeper.id
        reply.refresh_from_db()
        assert reply.commented_by_contact_id == keeper.id
        activity.refresh_from_db()
        assert activity.entity_id == keeper.id
        # Delivered about the loser, and left as sent.
        notice.refresh_from_db()
        assert notice.entity_id == loser.id

    def test_keeper_wins_and_its_blanks_take_the_losers_values(
        self, admin_client, org_a
    ):
        keeper = _contact(
            org_a, "Kay", "Keeper", title="CTO", custom_fields={"tier": "gold"}
        )
        loser = _contact(
            org_a,
            "Lou",
            "Loser",
            title="Intern",
            phone="202 555 0147",
            city="Oslo",
            do_not_call=True,
            custom_fields={"tier": "bronze", "lang": "no"},
        )
        _merge(admin_client, "contacts", keeper.id, loser.id)
        keeper.refresh_from_db()
        assert (keeper.first_name, keeper.last_name, keeper.title) == (
            "Kay",
            "Keeper",
            "CTO",
        )
        assert keeper.phone == "202 555 0147"
        assert keeper.city == "Oslo"
        assert keeper.custom_fields == {"tier": "gold", "lang": "no"}
        # A request not to be called survives whichever record carried it.
        assert keeper.do_not_call is True

    def test_owners_follow_the_keeper_wins_rule_and_tags_are_a_union(
        self, admin_client, org_a, admin_profile, user_profile
    ):
        keeper, loser = _contact(org_a), _contact(org_a)
        keeper.assigned_to.add(admin_profile)
        loser.assigned_to.add(user_profile)
        red = Tags.objects.create(org=org_a, name="red")
        blue = Tags.objects.create(org=org_a, name="blue")
        keeper.tags.add(red)
        loser.tags.add(red, blue)
        _merge(admin_client, "contacts", keeper.id, loser.id)
        assert list(keeper.assigned_to.all()) == [admin_profile]
        assert set(keeper.tags.all()) == {red, blue}

        # An unowned keeper takes the loser's owners.
        bare, owned = _contact(org_a), _contact(org_a)
        owned.assigned_to.add(user_profile)
        _merge(admin_client, "contacts", bare.id, owned.id)
        assert list(bare.assigned_to.all()) == [user_profile]

    def test_the_losers_portal_access_dies_and_its_address_moves_over(
        self, admin_client, org_a, client
    ):
        keeper = _contact(org_a, "Kay")
        loser = _contact(org_a, "Lou", email="lou@x.com")
        PortalLoginToken.objects.create(
            org=org_a,
            contact=loser,
            code_hash="x",
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        session = mint_portal_token(loser)
        assert (
            client.get(
                "/api/portal/cases/", HTTP_AUTHORIZATION=f"Bearer {session}"
            ).status_code
            == 200
        )

        _merge(admin_client, "contacts", keeper.id, loser.id)

        # Sign-in codes were minted for the loser and are not handed on.
        assert not PortalLoginToken.objects.filter(contact_id=loser.id).exists()
        assert not PortalLoginToken.objects.filter(contact=keeper).exists()
        # The loser's open portal session names a contact that is gone.
        assert (
            client.get(
                "/api/portal/cases/", HTTP_AUTHORIZATION=f"Bearer {session}"
            ).status_code
            == 401
        )
        # The keeper had no address, so it takes the loser's: the same thing
        # anybody who may edit the keeper could have typed in.
        keeper.refresh_from_db()
        assert keeper.email == "lou@x.com"

    def test_the_audit_log_names_both_records(self, admin_client, org_a, admin_user):
        keeper = _contact(org_a, "Kay", "Keeper")
        loser = _contact(org_a, "Lou", "Loser")
        _merge(admin_client, "contacts", keeper.id, loser.id)
        row = SecurityAuditLog.objects.get(event_type="RECORD_MERGED")
        assert row.org_id == org_a.id
        assert row.user_id == admin_user.id
        assert row.metadata == {
            "entity": "contact",
            "kept_id": str(keeper.id),
            "kept_name": "Kay Keeper",
            "merged_id": str(loser.id),
            "merged_name": "Lou Loser",
        }

    def test_the_losers_deletion_reaches_webhooks_as_a_delete(
        self, admin_client, org_a, admin_profile
    ):
        hook = WebhookEndpoint.objects.create(
            org=org_a,
            url="https://hooks.example.com/in",
            events=["contact.deleted", "contact.updated"],
            created_by=admin_profile.user,
        )
        keeper, loser = _contact(org_a, "Kay"), _contact(org_a, "Lou")
        with mock.patch("webhooks.emit.deliver_webhook.delay"):
            _merge(admin_client, "contacts", keeper.id, loser.id)
        events = {
            (d.event, d.payload["data"]["id"])
            for d in WebhookDelivery.objects.filter(endpoint=hook)
        }
        assert events == {
            ("contact.deleted", str(loser.id)),
            ("contact.updated", str(keeper.id)),
        }


class TestLeadMerge:
    def test_links_move_and_a_blank_email_is_filled_despite_the_unique_rule(
        self, admin_client, org_a
    ):
        keeper = Lead.objects.create(org=org_a, first_name="Kay", status="assigned")
        loser = Lead.objects.create(
            org=org_a, first_name="Lou", email="lou@x.com", status="closed"
        )
        person = _contact(org_a)
        loser.contacts.add(person)
        task = Task.objects.create(
            org=org_a, title="T", status="New", priority="Low", lead=loser
        )
        form = WebForm.objects.create(name="F", org=org_a)
        submission = WebFormSubmission.objects.create(
            org=org_a, form=form, lead=loser, status=WebFormSubmission.ACCEPTED
        )
        response = _merge(admin_client, "leads", keeper.id, loser.id)
        assert response.status_code == 200, response.content
        keeper.refresh_from_db()
        assert keeper.email == "lou@x.com"
        # Workflow position is the keeper's.
        assert keeper.status == "assigned"
        assert list(keeper.contacts.all()) == [person]
        task.refresh_from_db()
        submission.refresh_from_db()
        assert task.lead_id == submission.lead_id == keeper.id


class TestAccountMerge:
    def test_cascading_and_protected_links_move_instead_of_dying(
        self, admin_client, org_a
    ):
        keeper = Account.objects.create(org=org_a, name="Acme")
        loser = Account.objects.create(
            org=org_a, name="ACME, Inc.", website="https://acme.com"
        )
        person = _contact(org_a, account=loser)
        loser.contacts.add(person)
        case = Case.objects.create(
            org=org_a, name="C", status="New", priority="Low", account=loser
        )
        deal = Opportunity.objects.create(org=org_a, name="Deal", account=loser)
        order = Order.objects.create(org=org_a, name="O", account=loser)
        invoice = Invoice.objects.create(
            org=org_a, invoice_title="I", invoice_number="1", account=loser
        )
        task = Task.objects.create(
            org=org_a, title="T", status="New", priority="Low", account=loser
        )
        response = _merge(admin_client, "accounts", keeper.id, loser.id)
        assert response.status_code == 200, response.content
        for row in (case, deal, order, invoice, task):
            row.refresh_from_db()
            assert row.account_id == keeper.id
        person.refresh_from_db()
        assert person.account_id == keeper.id
        assert list(keeper.contacts.all()) == [person]
        keeper.refresh_from_db()
        assert keeper.name == "Acme"
        assert keeper.website == "https://acme.com"


class TestFieldsThatBelongTogether:
    @pytest.mark.parametrize(
        "keeper_currency, taken",
        [(None, True), ("EUR", True), ("USD", False)],
    )
    def test_an_amount_moves_only_with_a_compatible_currency(
        self, admin_client, org_a, keeper_currency, taken
    ):
        keeper = Lead.objects.create(
            org=org_a, first_name="K", currency=keeper_currency
        )
        loser = Lead.objects.create(
            org=org_a, first_name="L", opportunity_amount=500, currency="EUR"
        )
        _merge(admin_client, "leads", keeper.id, loser.id)
        keeper.refresh_from_db()
        if taken:
            assert (keeper.opportunity_amount, keeper.currency) == (500, "EUR")
        else:
            assert keeper.opportunity_amount is None
            assert keeper.currency == "USD"

    def test_a_currency_never_relabels_the_keepers_amount(self, admin_client, org_a):
        keeper = Account.objects.create(org=org_a, name="Acme", annual_revenue=100)
        loser = Account.objects.create(
            org=org_a, name="Acme Inc", annual_revenue=900, currency="INR"
        )
        _merge(admin_client, "accounts", keeper.id, loser.id)
        keeper.refresh_from_db()
        assert (keeper.annual_revenue, keeper.currency) == (100, None)

    def test_an_address_is_taken_whole_or_not_at_all(self, admin_client, org_a):
        partial = _contact(org_a, "Kay", city="Oslo")
        full = _contact(
            org_a, "Lou", address_line="1 Main St", city="Pune", country="IN"
        )
        _merge(admin_client, "contacts", partial.id, full.id)
        partial.refresh_from_db()
        assert (partial.address_line, partial.city, partial.country) == (
            None,
            "Oslo",
            None,
        )

        empty = _contact(org_a, "Em")
        other = _contact(
            org_a, "Ot", address_line="2 High St", city="Leeds", country="GB"
        )
        _merge(admin_client, "contacts", empty.id, other.id)
        empty.refresh_from_db()
        assert (empty.address_line, empty.city, empty.country) == (
            "2 High St",
            "Leeds",
            "GB",
        )

    def test_a_lead_name_is_taken_whole_or_not_at_all(self, admin_client, org_a):
        half = Lead.objects.create(org=org_a, first_name="Ann", email="a@x.com")
        other = Lead.objects.create(org=org_a, first_name="Bob", last_name="Smith")
        _merge(admin_client, "leads", half.id, other.id)
        half.refresh_from_db()
        assert (half.first_name, half.last_name) == ("Ann", None)

        nameless = Lead.objects.create(org=org_a, company_name="Acme")
        named = Lead.objects.create(
            org=org_a, salutation="Dr", first_name="Cy", last_name="Tan"
        )
        _merge(admin_client, "leads", nameless.id, named.id)
        nameless.refresh_from_db()
        assert (nameless.salutation, nameless.first_name, nameless.last_name) == (
            "Dr",
            "Cy",
            "Tan",
        )
