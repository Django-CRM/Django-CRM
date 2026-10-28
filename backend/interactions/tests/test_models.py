"""The interaction table, the lead's response fields and the org's stamp:
constraints, what deleting a record takes with it, and row-level security."""

import pytest
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from accounts.models import Account
from cases.models import Case
from common.duplicate_detection import merge_records
from common.models import Org
from common.rls import ORG_SCOPED_TABLES
from common.testing import org_tables_not_covered, rls_org, rls_policy_gaps
from contacts.models import Contact
from interactions.models import (
    CALL_OUTCOMES,
    DIRECTION_CHOICES,
    HOME_CHOICES,
    KIND_CHOICES,
    OUTBOUND_KINDS,
    Interaction,
)
from leads.models import Lead
from opportunity.models import Opportunity

HOMES = ("lead", "contact", "account", "opportunity", "case")


def _records(org):
    return {
        "lead": Lead.objects.create(org=org, first_name="Ada", last_name="L"),
        "contact": Contact.objects.create(org=org, first_name="Ann", last_name="Lee"),
        "account": Account.objects.create(org=org, name="Acme"),
        "opportunity": Opportunity.objects.create(org=org, name="Deal"),
        "case": Case.objects.create(
            org=org, name="Ticket", status="New", priority="Normal"
        ),
    }


def _log(org, home, record, **fields):
    return Interaction.objects.create(
        org=org, kind="call", home=home, **{home: record, **fields}
    )


def test_the_constants_are_the_contracts():
    assert [k for k, _ in KIND_CHOICES] == [
        "call",
        "meeting",
        "email",
        "whatsapp",
        "sms",
        "enquiry",
    ]
    assert OUTBOUND_KINDS == {"call", "meeting", "email", "whatsapp", "sms"}
    assert [k for k, _ in CALL_OUTCOMES] == [
        "connected",
        "no_answer",
        "voicemail",
        "busy",
        "wrong_number",
    ]
    assert [k for k, _ in DIRECTION_CHOICES] == ["inbound", "outbound"]
    assert [k for k, _ in HOME_CHOICES] == list(HOMES)


def test_an_interaction_starts_with_the_contracts_defaults(org_a):
    before = timezone.now()
    lead = Lead.objects.create(org=org_a, first_name="Ada")
    row = _log(org_a, "lead", lead)
    row.refresh_from_db()
    assert row.direction == "outbound"
    assert before <= row.occurred_at <= timezone.now()
    assert row.duration_seconds is None
    assert row.duration_estimated is False
    assert (row.outcome, row.subject, row.body, row.external_id) == ("", "", "", "")
    assert row.participants == []
    assert row.origin == "manual"
    assert row.logged_by is None
    assert list(lead.interactions.all()) == [row]


def test_logged_by_is_kept_as_null_when_the_profile_goes(org_a, user_profile):
    lead = Lead.objects.create(org=org_a, first_name="Ada")
    row = _log(org_a, "lead", lead, logged_by=user_profile)
    assert list(user_profile.logged_interactions.all()) == [row]
    user_profile.delete()
    row.refresh_from_db()
    assert row.logged_by is None


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


def test_one_external_id_per_origin_in_an_org(org_a, org_b):
    lead = Lead.objects.create(org=org_a, first_name="Ada")
    _log(org_a, "lead", lead, origin="intake", external_id="msg-1")
    with pytest.raises(IntegrityError), transaction.atomic():
        _log(org_a, "lead", lead, origin="intake", external_id="msg-1")

    _log(org_a, "lead", lead, origin="email", external_id="msg-1")
    with rls_org(org_b):
        theirs = Lead.objects.create(org=org_b, first_name="Bo")
        _log(org_b, "lead", theirs, origin="intake", external_id="msg-1")


def test_a_blank_external_id_repeats_freely(org_a):
    lead = Lead.objects.create(org=org_a, first_name="Ada")
    _log(org_a, "lead", lead, origin="intake")
    _log(org_a, "lead", lead, origin="intake")
    assert Interaction.objects.filter(lead=lead).count() == 2


def test_the_home_must_be_one_of_the_records_it_links(org_a):
    records = _records(org_a)
    with pytest.raises(IntegrityError), transaction.atomic():
        Interaction.objects.create(
            org=org_a, kind="call", home="lead", contact=records["contact"]
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        Interaction.objects.create(
            org=org_a, kind="call", home="task", lead=records["lead"]
        )
    _log(org_a, "contact", records["contact"], lead=records["lead"])


# ---------------------------------------------------------------------------
# Deleting a record
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("home", HOMES)
def test_a_deleted_record_takes_what_it_is_home_to_and_unlinks_the_rest(org_a, home):
    records = _records(org_a)
    other = "contact" if home == "lead" else "lead"
    homed = _log(org_a, home, records[home])
    linked = _log(org_a, other, records[other], **{home: records[home]})
    elsewhere = _log(org_a, other, records[other])

    records[home].delete()

    assert not Interaction.objects.filter(pk=homed.pk).exists()
    linked.refresh_from_db()
    assert getattr(linked, f"{home}_id") is None
    assert linked.home == other
    assert getattr(linked, f"{other}_id") == records[other].pk
    assert Interaction.objects.filter(pk=elsewhere.pk).exists()


def test_a_bulk_delete_follows_the_same_rule(org_a):
    records = _records(org_a)
    lead = records["lead"]
    homed = _log(org_a, "lead", lead)
    linked = _log(org_a, "contact", records["contact"], lead=lead)

    Lead.objects.filter(pk=lead.pk).delete()

    assert not Interaction.objects.filter(pk=homed.pk).exists()
    linked.refresh_from_db()
    assert linked.lead_id is None


def test_a_cascaded_delete_follows_the_same_rule(org_a):
    """An account's tickets go with it, and so do the tickets' interactions."""
    account = Account.objects.create(org=org_a, name="Acme")
    ticket = Case.objects.create(
        org=org_a, name="Ticket", status="New", priority="Normal", account=account
    )
    contact = Contact.objects.create(org=org_a, first_name="Ann", last_name="Lee")
    on_ticket = _log(org_a, "case", ticket)
    on_account = _log(org_a, "account", account)
    mentions_ticket = _log(org_a, "contact", contact, case=ticket, account=account)

    account.delete()

    assert not Interaction.objects.filter(pk__in=[on_ticket.pk, on_account.pk]).exists()
    mentions_ticket.refresh_from_db()
    assert (mentions_ticket.case_id, mentions_ticket.account_id) == (None, None)
    assert mentions_ticket.contact_id == contact.pk


def test_a_merge_moves_the_losers_interactions_to_the_keeper(org_a):
    keeper = Lead.objects.create(org=org_a, first_name="Ada", email="a@example.com")
    loser = Lead.objects.create(org=org_a, first_name="Ada", email="b@example.com")
    row = _log(org_a, "lead", loser)

    with transaction.atomic():
        merge_records(keeper, loser)

    row.refresh_from_db()
    assert (row.home, row.lead_id) == ("lead", keeper.pk)


# ---------------------------------------------------------------------------
# The lead's response fields and the org's stamp
# ---------------------------------------------------------------------------


def test_a_lead_starts_unanswered_and_names_its_first_responder(org_a, user_profile):
    lead = Lead.objects.create(org=org_a, first_name="Ada")
    lead.refresh_from_db()
    assert (lead.first_response_at, lead.first_response_by, lead.last_outbound_at) == (
        None,
        None,
        None,
    )

    now = timezone.now()
    lead.first_response_at = lead.last_outbound_at = now
    lead.first_response_by = user_profile
    lead.save()
    assert list(user_profile.first_responses.all()) == [lead]

    user_profile.delete()
    lead.refresh_from_db()
    assert lead.first_response_by is None
    assert lead.first_response_at == now


def test_the_indexes_the_timelines_and_the_unanswered_list_read():
    def fields(model):
        return [list(index.fields) for index in model._meta.indexes]

    assert fields(Interaction) == [
        ["org", "-created_at"],
        ["org", "lead", "occurred_at"],
        ["org", "contact", "occurred_at"],
        ["org", "opportunity", "occurred_at"],
    ]
    assert ["org", "status", "last_outbound_at"] in fields(Lead)


def test_a_new_org_is_stamped_when_it_is_created():
    before = timezone.now()
    org = Org.objects.create(name="New")
    org.refresh_from_db()
    assert before <= org.lead_response_since <= timezone.now()


@pytest.mark.django_db(transaction=True)
def test_an_existing_org_is_stamped_when_the_column_is_added():
    """Not at its creation: that would put years of its open leads in the
    unanswered list on the first day."""

    def migrate(target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([target])
        return executor.loader.project_state(target).apps

    old = migrate(("common", "0058_saved_view_lead_source_keys"))
    try:
        legacy = old.get_model("common", "Org").objects.create(name="Legacy Co")
        before = timezone.now()
        migrate(("common", "0059_org_lead_response_since"))
        # One column, so an Org field added after 0059 cannot break the read.
        stamp = (
            Org.objects.filter(pk=legacy.pk)
            .values_list("lead_response_since", flat=True)
            .get()
        )
        assert legacy.created_at < before <= stamp <= timezone.now()
    finally:
        call_command("migrate", verbosity=0)
        Org.objects.filter(name="Legacy Co").delete()


# ---------------------------------------------------------------------------
# Row-level security
# ---------------------------------------------------------------------------


def test_the_table_is_registered_for_rls():
    assert "interaction" in ORG_SCOPED_TABLES
    assert org_tables_not_covered() == []


@pytest.mark.postgres_only
def test_the_table_has_its_policies():
    if connection.vendor != "postgresql":
        pytest.skip("RLS policies exist only on PostgreSQL")
    assert rls_policy_gaps(["interaction"]) == []


def _need_rls():
    if connection.vendor != "postgresql":
        pytest.skip("Row-level security is a PostgreSQL feature")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        if cursor.fetchone()[0]:
            pytest.skip("Needs a role row-level security binds, as in CI")


@pytest.mark.postgres_only
def test_another_orgs_interactions_are_invisible(org_a, org_b):
    _need_rls()
    mine = _log(org_a, "lead", Lead.objects.create(org=org_a, first_name="Ada"))
    with rls_org(org_b):
        theirs = _log(org_b, "lead", Lead.objects.create(org=org_b, first_name="Bo"))
        assert list(Interaction.objects.all()) == [theirs]

    assert list(Interaction.objects.all()) == [mine]
    assert not Interaction.objects.filter(pk=theirs.pk).exists()


@pytest.mark.postgres_only
def test_an_interaction_cannot_be_written_into_another_org(org_a, org_b):
    _need_rls()
    with rls_org(org_b):
        theirs = Lead.objects.create(org=org_b, first_name="Bo")
    with pytest.raises(DatabaseError), transaction.atomic():
        Interaction.objects.create(org=org_b, kind="call", home="lead", lead=theirs)
