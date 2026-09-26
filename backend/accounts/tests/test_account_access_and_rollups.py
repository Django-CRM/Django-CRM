"""Object-level access on an account, and the numbers the account page prints.

Written while wiring `/v2/accounts` to the real API. Every case here failed
against the code as it stood, or pins a behaviour that was one edit away from
breaking:

1. `get`, `put`, `patch` and comment `post` compared `request.profile`, a
   Profile: against `account.created_by`, a FK to `User`. Never equal, so the
   creator branch was unreachable. `delete()` and the list filter compared
   correctly, which is how you can tell: the same non-admin could see an
   account in their own list, be refused permission to open it, and delete it
   anyway.
2. `users_mention` read `created_by.user.email`. `created_by` *is* the User, so
   that was an AttributeError, a 500 for every non-admin assignee who opened
   an account they were legitimately assigned.
3. Commenting on an id that does not exist raised `DoesNotExist` (500) where
   `get` on the same id answered 404.
4. `AccountAttachmentView.delete` looked its row up by primary key with no org
   filter. `Attachments` is one generic table shared by every module, so that
   endpoint deleted any attachment in the database, any org, any parent record.
5. A negative `annual_revenue` reached the CheckConstraint and came back as an
   IntegrityError, i.e. a 500 naming no field.
6. The account page's money had no source at all. `won_amount`, `open_pipeline`,
   `overdue_amount` and the counts beside them are now annotated in SQL, and
   the interesting case is an account with several deals *and* several invoices,
   a single `.annotate(Sum(...), Sum(...))` multiplies those together.
"""

import datetime
from decimal import Decimal

import pytest
from django.contrib.contenttypes.models import ContentType

from accounts.models import Account
from cases.models import Case
from common.models import Attachments, Profile
from invoices.models import Invoice
from leads.models import Lead
from opportunity.models import Opportunity


def _invoice(org, account, number, amount_due, due_date, status="Sent"):
    """An invoice owing a known amount.

    `Invoice.save()` calls `recalculate_totals()`, which rebuilds `total_amount`
    from the line items and then derives `amount_due` from it. An invoice with
    no line items therefore comes back owing zero no matter what was passed to
    `create()`, which silently turns every overdue assertion into 0 == 0. The
    `.update()` writes the figures the way a real invoice with line items would
    have ended up carrying them, without needing to model the line items.
    """
    invoice = Invoice.objects.create(
        invoice_title=number,
        invoice_number=number,
        org=org,
        account=account,
        status=status,
        due_date=due_date,
    )
    Invoice.objects.filter(pk=invoice.pk).update(
        total_amount=amount_due, amount_paid=Decimal("0"), amount_due=amount_due
    )
    invoice.refresh_from_db()
    return invoice


def _created_by(account, user):
    """Stage a creator.

    `BaseModel.save()` overwrites `created_by` from the crum thread-local, so
    passing it to `create()` does not stick. The row comes back with whoever
    the request belongs to, or None. `.update()` skips `save()` and is the only
    way to set it. Getting this wrong makes every creator test a false negative:
    the branch under test is simply never entered.
    """
    Account.objects.filter(pk=account.pk).update(created_by=user)
    account.refresh_from_db()
    return account


@pytest.fixture
def account(org_a):
    return Account.objects.create(name="Northwind Traders", org=org_a)


@pytest.mark.django_db
class TestAccountDetailAccess:
    """Who may open an account. Both answers, for every kind of caller."""

    def test_admin_can_open_any_account(self, admin_client, account):
        response = admin_client.get(f"/api/accounts/{account.id}/")
        assert response.status_code == 200

    def test_assigned_non_admin_can_open_it(
        self, user_client, user_profile, account, admin_user
    ):
        """The 500 regression.

        An assignee has always been allowed in. What they got was an
        AttributeError, because the mention list took the created_by branch and
        read `.user` off a User.
        """
        _created_by(account, admin_user)
        account.assigned_to.add(user_profile)

        response = user_client.get(f"/api/accounts/{account.id}/")

        assert response.status_code == 200
        assert response.json()["users_mention"] == [{"user__email": admin_user.email}]

    def test_creator_can_open_their_own_account(
        self, user_client, user_profile, account
    ):
        _created_by(account, user_profile.user)
        response = user_client.get(f"/api/accounts/{account.id}/")
        assert response.status_code == 200

    def test_unrelated_non_admin_is_refused(self, user_client, account, admin_user):
        _created_by(account, admin_user)
        response = user_client.get(f"/api/accounts/{account.id}/")
        assert response.status_code == 403

    def test_another_org_gets_404_not_403(self, org_b_client, account):
        """Wrong tenant is 'no such account', not 'not allowed'.

        403 would confirm the id exists to somebody who should not be able to
        learn even that.
        """
        response = org_b_client.get(f"/api/accounts/{account.id}/")
        assert response.status_code == 404

    def test_account_with_no_creator_still_refuses_strangers(
        self, user_client, account
    ):
        """`created_by` is null on seeded and imported rows.

        `None == None` must not become a way in for whoever asks first.
        """
        assert account.created_by is None
        response = user_client.get(f"/api/accounts/{account.id}/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestAccountVerbsAgree:
    """The four verbs used to disagree with each other. Pin that they cannot."""

    def test_creator_sees_it_listed_and_can_open_it(
        self, user_client, user_profile, account
    ):
        """The contradiction, in one test.

        The list filter used `created_by=profile.user` (right) while `get` used
        `profile == created_by` (wrong), so this account appeared in a list the
        same person was forbidden to open.
        """
        _created_by(account, user_profile.user)

        listed = user_client.get("/api/accounts/").json()
        ids = [a["id"] for a in listed["active_accounts"]["open_accounts"]]
        assert str(account.id) in ids

        assert user_client.get(f"/api/accounts/{account.id}/").status_code == 200

    def test_creator_can_delete_and_could_always_delete(
        self, user_client, user_profile, account
    ):
        """`delete` was the verb that was already correct.

        It stays correct. The point of the fix was to bring the other four up
        to it, not to take this one away.
        """
        _created_by(account, user_profile.user)
        response = user_client.delete(f"/api/accounts/{account.id}/")
        assert response.status_code == 200
        assert not Account.objects.filter(pk=account.pk).exists()

    def test_creator_can_patch(self, user_client, user_profile, account):
        _created_by(account, user_profile.user)
        response = user_client.patch(
            f"/api/accounts/{account.id}/",
            {"city": "Liverpool"},
            content_type="application/json",
        )
        assert response.status_code == 200
        account.refresh_from_db()
        assert account.city == "Liverpool"

    def test_unrelated_non_admin_cannot_patch(self, user_client, account, admin_user):
        _created_by(account, admin_user)
        response = user_client.patch(
            f"/api/accounts/{account.id}/",
            {"city": "Nowhere"},
            content_type="application/json",
        )
        assert response.status_code == 403
        account.refresh_from_db()
        assert account.city is None

    def test_unrelated_non_admin_cannot_put(self, user_client, account, admin_user):
        _created_by(account, admin_user)
        response = user_client.put(
            f"/api/accounts/{account.id}/",
            {"name": "Renamed By A Stranger"},
            content_type="application/json",
        )
        assert response.status_code == 403
        account.refresh_from_db()
        assert account.name == "Northwind Traders"

    def test_put_refuses_before_it_validates(self, user_client, account, admin_user):
        """A stranger with a malformed body is still a stranger.

        The access check used to sit inside `is_valid()`, so an unauthorised
        caller was told their payload was bad, a free validity oracle on
        somebody else's record, before being refused.
        """
        _created_by(account, admin_user)
        response = user_client.put(
            f"/api/accounts/{account.id}/",
            {"name": ""},
            content_type="application/json",
        )
        assert response.status_code == 403

    def test_comment_on_missing_account_is_404(self, admin_client):
        response = admin_client.post(
            "/api/accounts/00000000-0000-0000-0000-000000000000/",
            {"comment": "hello"},
        )
        assert response.status_code == 404

    def test_unrelated_non_admin_cannot_comment(self, user_client, account, admin_user):
        _created_by(account, admin_user)
        response = user_client.post(
            f"/api/accounts/{account.id}/", {"comment": "hello"}
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestAttachmentDeleteIsScoped:
    """The generic attachments table is shared by every module."""

    def _attachment(self, org, account):
        return Attachments.objects.create(
            content_type=ContentType.objects.get_for_model(Account),
            object_id=account.id,
            file_name="contract.pdf",
            attachment="attachments/contract.pdf",
            org=org,
        )

    def test_cannot_delete_another_orgs_attachment(self, admin_client, org_b, org_a):
        """The cross-tenant delete.

        Admin of org A, attachment belonging to org B, looked up by primary key
        alone. This answered 200 and the row was gone.
        """
        their_account = Account.objects.create(name="Someone Else Ltd", org=org_b)
        theirs = self._attachment(org_b, their_account)

        response = admin_client.delete(f"/api/accounts/attachment/{theirs.id}/")

        assert response.status_code == 404
        assert Attachments.objects.filter(pk=theirs.pk).exists()

    def test_admin_can_delete_their_own_orgs_attachment(
        self, admin_client, org_a, account
    ):
        mine = self._attachment(org_a, account)
        response = admin_client.delete(f"/api/accounts/attachment/{mine.id}/")
        assert response.status_code == 200
        assert not Attachments.objects.filter(pk=mine.pk).exists()

    def test_uploader_can_delete_their_own_attachment(
        self, user_client, user_profile, org_a, account
    ):
        """`created_by` is a User FK here too.

        Comparing the Profile made this branch dead, so deleting an attachment
        was quietly admin-only.
        """
        mine = self._attachment(org_a, account)
        Attachments.objects.filter(pk=mine.pk).update(created_by=user_profile.user)

        response = user_client.delete(f"/api/accounts/attachment/{mine.id}/")

        assert response.status_code == 200
        assert not Attachments.objects.filter(pk=mine.pk).exists()

    def test_other_users_attachment_is_refused(
        self, user_client, admin_user, org_a, account
    ):
        theirs = self._attachment(org_a, account)
        Attachments.objects.filter(pk=theirs.pk).update(created_by=admin_user)

        response = user_client.delete(f"/api/accounts/attachment/{theirs.id}/")

        assert response.status_code == 403
        assert Attachments.objects.filter(pk=theirs.pk).exists()

    def test_missing_attachment_is_404_not_500(self, admin_client):
        response = admin_client.delete(
            "/api/accounts/attachment/00000000-0000-0000-0000-000000000000/"
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestMalformedIds:
    """A stale bookmark should not read as a server fault."""

    def test_non_uuid_account_id_is_404(self, admin_client):
        """`acc-northwind` is what the v2 fixtures used for ids.

        The URL pattern is `<str:pk>`, so the text reaches `UUIDField` and
        `to_python` raises `ValidationError`, which `get_object_or_404` does
        not catch, so every typo and every old link answered 500.
        """
        response = admin_client.get("/api/accounts/acc-northwind/")
        assert response.status_code == 404

    def test_non_uuid_attachment_id_is_404(self, admin_client):
        response = admin_client.delete("/api/accounts/attachment/not-a-uuid/")
        assert response.status_code == 404

    def test_non_uuid_patch_is_404(self, admin_client):
        response = admin_client.patch(
            "/api/accounts/12345/", {"city": "X"}, content_type="application/json"
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestRevenueValidation:
    def test_negative_revenue_is_400_naming_the_field(self, admin_client):
        response = admin_client.post(
            "/api/accounts/", {"name": "Underwater Ltd", "annual_revenue": "-5000"}
        )
        assert response.status_code == 400
        assert "annual_revenue" in response.json()["errors"]
        assert not Account.objects.filter(name="Underwater Ltd").exists()

    def test_zero_revenue_is_allowed(self, admin_client):
        response = admin_client.post(
            "/api/accounts/", {"name": "Pre Revenue Ltd", "annual_revenue": "0"}
        )
        assert response.status_code == 200

    def test_negative_headcount_still_400(self, admin_client):
        """The sibling field already answered correctly; keep it that way."""
        response = admin_client.post(
            "/api/accounts/", {"name": "Ghost Ltd", "number_of_employees": -3}
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestAccountRollups:
    """The numbers on the account page, and the join that would multiply them."""

    def _rollups(self, client, account):
        response = client.get(f"/api/accounts/{account.id}/")
        assert response.status_code == 200
        return response.json()["account_obj"]["rollups"]

    def test_won_deals_are_the_lifetime_value(self, admin_client, org_a, account):
        Opportunity.objects.create(
            name="Won one",
            org=org_a,
            account=account,
            stage="CLOSED_WON",
            amount=Decimal("1000"),
            closed_on=datetime.date(2024, 3, 1),
        )
        Opportunity.objects.create(
            name="Lost one",
            org=org_a,
            account=account,
            stage="CLOSED_LOST",
            amount=Decimal("9999"),
            closed_on=datetime.date(2024, 4, 1),
        )

        rollups = self._rollups(admin_client, account)

        assert Decimal(str(rollups["won_amount"])) == Decimal("1000")
        assert rollups["won_count"] == 1
        assert rollups["first_won_on"] == "2024-03-01"

    def test_open_pipeline_excludes_closed_deals(self, admin_client, org_a, account):
        Opportunity.objects.create(
            name="Live",
            org=org_a,
            account=account,
            stage="NEGOTIATION",
            amount=Decimal("500"),
        )
        Opportunity.objects.create(
            name="Won",
            org=org_a,
            account=account,
            stage="CLOSED_WON",
            amount=Decimal("700"),
            closed_on=datetime.date(2024, 1, 1),
        )

        rollups = self._rollups(admin_client, account)

        assert Decimal(str(rollups["open_pipeline"])) == Decimal("500")
        assert rollups["open_deal_count"] == 1

    def test_nothing_is_zero_not_null(self, admin_client, account):
        """An account with no deals, invoices or tickets.

        Each subquery returns no rows at all, and an empty subquery is NULL,
        so without the Coalesce every figure on a brand new account renders as
        blank rather than zero.
        """
        rollups = self._rollups(admin_client, account)

        assert Decimal(str(rollups["won_amount"])) == Decimal("0")
        assert Decimal(str(rollups["open_pipeline"])) == Decimal("0")
        assert Decimal(str(rollups["overdue_amount"])) == Decimal("0")
        assert rollups["won_count"] == 0
        assert rollups["open_deal_count"] == 0
        assert rollups["open_tickets"] == 0
        assert rollups["first_won_on"] is None

    def test_deals_and_invoices_do_not_multiply_each_other(
        self, admin_client, org_a, account
    ):
        """The reason every aggregate is its own subquery.

        Two deals and three invoices on one account. Annotated together on a
        single queryset the joins produce six rows, and both totals come back
        tripled and doubled respectively. This test fails loudly if anyone
        collapses them back into one `.annotate()`.
        """
        for i in range(2):
            Opportunity.objects.create(
                name=f"Deal {i}",
                org=org_a,
                account=account,
                stage="PROPOSAL",
                amount=Decimal("100"),
            )
        for i in range(3):
            _invoice(
                org_a, account, f"MULT-{i}", Decimal("50"), datetime.date(2020, 1, 1)
            )

        rollups = self._rollups(admin_client, account)

        assert Decimal(str(rollups["open_pipeline"])) == Decimal("200")
        assert rollups["open_deal_count"] == 2
        assert Decimal(str(rollups["overdue_amount"])) == Decimal("150")

    def test_overdue_counts_a_past_due_invoice_still_marked_sent(
        self, admin_client, org_a, account
    ):
        """`status == "Overdue"` is a nightly task's opinion, and it lags.

        Keying off the status alone hides money on any day the task has not run,
        which on the seeded database is most of them.
        """
        _invoice(org_a, account, "LATE-1", Decimal("400"), datetime.date(2020, 1, 1))

        rollups = self._rollups(admin_client, account)

        assert Decimal(str(rollups["overdue_amount"])) == Decimal("400")

    def test_paid_and_future_invoices_are_not_overdue(
        self, admin_client, org_a, account
    ):
        # Settled: past due, but there is nothing left to collect.
        _invoice(
            org_a,
            account,
            "PAID-1",
            Decimal("0"),
            datetime.date(2020, 1, 1),
            status="Paid",
        )
        # Billed, unpaid, and not due for another seventy years.
        _invoice(org_a, account, "FUT-1", Decimal("100"), datetime.date(2099, 1, 1))

        rollups = self._rollups(admin_client, account)

        assert Decimal(str(rollups["overdue_amount"])) == Decimal("0")

    def test_open_tickets_uses_the_ticket_modules_definition(
        self, admin_client, org_a, account
    ):
        """Same terminal statuses as `cases.workflow`.

        An account claiming three open tickets while the tickets page lists one
        is worse than an account claiming nothing.
        """
        for status in ("New", "Assigned", "Pending", "Closed", "Rejected", "Duplicate"):
            Case.objects.create(
                name=f"Ticket {status}",
                org=org_a,
                account=account,
                status=status,
                priority="Normal",
                case_type="Question",
            )

        rollups = self._rollups(admin_client, account)

        assert rollups["open_tickets"] == 3

    def test_another_orgs_records_never_count(
        self, admin_client, org_a, org_b, account
    ):
        """Deals belong to an account, and an account belongs to one org.

        Pinned anyway: the aggregate walks a relation, so a cross-org row
        hanging off this account used to be counted (this test asserted 877).
        Every roll-up now filters on the account's org too, which is what RLS
        would have answered on Postgres.
        """
        Opportunity.objects.create(
            name="Theirs",
            org=org_b,
            account=account,
            stage="PROPOSAL",
            amount=Decimal("777"),
        )
        Opportunity.objects.create(
            name="Mine",
            org=org_a,
            account=account,
            stage="PROPOSAL",
            amount=Decimal("100"),
        )

        response = admin_client.get(f"/api/accounts/{account.id}/")
        rollups = response.json()["account_obj"]["rollups"]

        assert Decimal(str(rollups["open_pipeline"])) == Decimal("100")
        assert rollups["open_deal_count"] == 1

    def test_the_list_carries_the_same_numbers_as_the_detail_page(
        self, admin_client, org_a, account
    ):
        """Same annotation, both endpoints.

        Two separate implementations is how a list and a record end up
        describing the same company differently.
        """
        Opportunity.objects.create(
            name="Live",
            org=org_a,
            account=account,
            stage="QUALIFICATION",
            amount=Decimal("250"),
        )

        listed = admin_client.get("/api/accounts/").json()
        row = next(
            a
            for a in listed["active_accounts"]["open_accounts"]
            if a["id"] == str(account.id)
        )
        detail = self._rollups(admin_client, account)

        assert row["rollups"] == detail
        assert Decimal(str(row["rollups"]["open_pipeline"])) == Decimal("250")

    def test_a_non_admin_sees_rollups_for_the_accounts_they_can_see(
        self, user_client, user_profile, org_a, account
    ):
        """The list narrows for non-admins; the numbers must survive it.

        The deal is the member's own: rollups count only deals the viewer may
        open (see `test_rollups_viewer_scoped.py`).
        """
        account.assigned_to.add(user_profile)
        deal = Opportunity.objects.create(
            name="Live",
            org=org_a,
            account=account,
            stage="PROPOSAL",
            amount=Decimal("300"),
        )
        deal.assigned_to.add(user_profile)

        listed = user_client.get("/api/accounts/").json()
        rows = listed["active_accounts"]["open_accounts"]

        assert [a["id"] for a in rows] == [str(account.id)]
        assert Decimal(str(rows[0]["rollups"]["open_pipeline"])) == Decimal("300")


def _money(rows, field):
    """``{currency: Decimal(field)}`` from a ``by_currency`` list, zeros dropped."""
    return {r["currency"]: Decimal(r[field]) for r in rows if Decimal(r[field]) != 0}


@pytest.mark.django_db
class TestRollupsNeverAddCurrencies:
    """Deals and invoices each carry a currency and there are no exchange rates.

    `won_amount`, `open_pipeline` and `overdue_amount` were one `Sum` each over
    every row, so a deal in USD and one in EUR came back as a single number.
    They now follow the invoice reports' convention: the plain figure when at
    most one currency is present, `None` when several, and `by_currency`.
    """

    def _rollups(self, client, account):
        response = client.get(f"/api/accounts/{account.id}/")
        assert response.status_code == 200
        return response.json()["account_obj"]["rollups"]

    def _deal(self, org, account, amount, currency, stage="PROPOSAL"):
        return Opportunity.objects.create(
            name=f"{currency} {amount}",
            org=org,
            account=account,
            stage=stage,
            amount=Decimal(amount),
            currency=currency,
            closed_on=datetime.date(2024, 1, 1) if stage == "CLOSED_WON" else None,
        )

    def test_one_currency_keeps_its_plain_figures(self, admin_client, org_a, account):
        self._deal(org_a, account, "500", "EUR", stage="CLOSED_WON")
        self._deal(org_a, account, "200", "EUR")

        rollups = self._rollups(admin_client, account)

        assert Decimal(rollups["won_amount"]) == Decimal("500")
        assert Decimal(rollups["open_pipeline"]) == Decimal("200")
        assert Decimal(rollups["overdue_amount"]) == Decimal("0")
        assert [r["currency"] for r in rollups["by_currency"]] == ["EUR"]

    def test_two_currencies_are_never_summed(self, admin_client, org_a, account):
        self._deal(org_a, account, "1000", "USD", stage="CLOSED_WON")
        self._deal(org_a, account, "300", "EUR", stage="CLOSED_WON")
        self._deal(org_a, account, "50", "USD")
        overdue = _invoice(
            org_a, account, "EUR-1", Decimal("70"), datetime.date(2020, 1, 1)
        )
        Invoice.objects.filter(pk=overdue.pk).update(currency="EUR")

        rollups = self._rollups(admin_client, account)

        for field in ("won_amount", "open_pipeline", "overdue_amount"):
            assert rollups[field] is None, field
        assert rollups["won_count"] == 2
        assert _money(rollups["by_currency"], "won_amount") == {
            "EUR": Decimal("300"),
            "USD": Decimal("1000"),
        }
        assert _money(rollups["by_currency"], "open_pipeline") == {"USD": Decimal("50")}
        assert _money(rollups["by_currency"], "overdue_amount") == {
            "EUR": Decimal("70")
        }

    def test_a_deal_with_no_currency_is_in_the_orgs_default(
        self, admin_client, org_a, account
    ):
        """What the deal serializer assumes when it fills a blank currency."""
        org_a.default_currency = "INR"
        org_a.save()
        Opportunity.objects.create(
            name="No currency",
            org=org_a,
            account=account,
            stage="PROPOSAL",
            amount=Decimal("40"),
        )
        self._deal(org_a, account, "60", "INR")

        rollups = self._rollups(admin_client, account)

        assert Decimal(rollups["open_pipeline"]) == Decimal("100")
        assert [r["currency"] for r in rollups["by_currency"]] == ["INR"]

    def test_the_list_splits_by_currency_too(self, admin_client, org_a, account):
        self._deal(org_a, account, "10", "USD")
        self._deal(org_a, account, "20", "GBP")

        listed = admin_client.get("/api/accounts/").json()
        (row,) = listed["active_accounts"]["open_accounts"]

        assert row["rollups"]["open_pipeline"] is None
        assert _money(row["rollups"]["by_currency"], "open_pipeline") == {
            "GBP": Decimal("20"),
            "USD": Decimal("10"),
        }


@pytest.mark.django_db
class TestRollupsAreAbsentNotZero:
    def test_serializer_omits_rollups_when_not_annotated(self, org_a):
        """Null, not a row of zeroes.

        `AccountSerializer` is used in places that do not annotate. Filling in
        zeroes there would state a fact nobody computed.
        """
        from accounts.serializer import AccountSerializer

        plain = Account.objects.create(name="Unannotated Ltd", org=org_a)
        assert AccountSerializer(plain).data["rollups"] is None

    def test_profile_count_unchanged(self, org_a, admin_profile):
        """Guard rail for the fixtures above, not a behaviour of the API."""
        assert Profile.objects.filter(org=org_a).count() >= 1


@pytest.mark.django_db
class TestSidePayloadsRespectRole:
    """The lead and contact catalogues served beside the account list.

    Found during the 2026-08-05 mobile parity review, by comparing what one
    member could read through two endpoints in the same second. `/api/accounts/`
    narrowed its account list correctly and then served the org's entire lead
    catalogue next to it, serialized in full: email, phone, opportunity_amount,
    address. A member entitled to 6 leads received all 20, including leads whose
    own detail route answers them 403.

    `/api/cases/` and `/api/opportunities/` already narrowed their equivalent
    payloads. These two were missed, so the tests below pin both directions:
    an admin still gets the whole catalogue, a member gets only their own.
    """

    def _lead(self, org, first_name, **kwargs):
        return Lead.objects.create(
            first_name=first_name,
            last_name="Prospect",
            email=f"{first_name.lower()}@example.com",
            phone="555-0100",
            org=org,
            status="assigned",
            **kwargs,
        )

    def test_admin_sees_every_lead_in_the_catalogue(
        self, admin_client, org_a, admin_user
    ):
        mine = self._lead(org_a, "Mine")
        theirs = self._lead(org_a, "Theirs")

        payload = admin_client.get("/api/accounts/").json()

        ids = {row["id"] for row in payload["leads"]}
        assert {str(mine.id), str(theirs.id)} <= ids

    def test_member_sees_only_leads_they_own(
        self, user_client, user_profile, org_a, admin_user
    ):
        assigned = self._lead(org_a, "Assigned")
        assigned.assigned_to.add(user_profile)
        created = self._lead(org_a, "Created")
        Lead.objects.filter(pk=created.pk).update(created_by=user_profile.user)
        stranger = self._lead(org_a, "Stranger")
        Lead.objects.filter(pk=stranger.pk).update(created_by=admin_user)

        payload = user_client.get("/api/accounts/").json()

        ids = {row["id"] for row in payload["leads"]}
        assert str(assigned.id) in ids
        assert str(created.id) in ids
        assert str(stranger.id) not in ids

    def test_catalogue_cannot_outrun_the_detail_route(
        self, user_client, org_a, admin_user
    ):
        """The two doors must agree.

        This is the actual defect: the same lead id, refused by its own
        endpoint and handed over by this one. Asserting the 403 here rather
        than only the absence keeps the test honest if the detail rule moves.
        """
        stranger = self._lead(org_a, "Stranger")
        Lead.objects.filter(pk=stranger.pk).update(created_by=admin_user)

        assert user_client.get(f"/api/leads/{stranger.id}/").status_code == 403

        payload = user_client.get("/api/accounts/").json()
        assert str(stranger.id) not in {row["id"] for row in payload["leads"]}

    def test_member_sees_only_contacts_they_own(
        self, user_client, user_profile, org_a, admin_user
    ):
        from contacts.models import Contact

        mine = Contact.objects.create(first_name="Mine", last_name="Contact", org=org_a)
        mine.assigned_to.add(user_profile)
        theirs = Contact.objects.create(
            first_name="Theirs", last_name="Contact", org=org_a
        )
        Contact.objects.filter(pk=theirs.pk).update(created_by=admin_user)

        payload = user_client.get("/api/accounts/").json()

        ids = {str(row["id"]) for row in payload["contacts"]}
        assert str(mine.id) in ids
        assert str(theirs.id) not in ids

    def test_admin_sees_every_contact(self, admin_client, org_a):
        from contacts.models import Contact

        one = Contact.objects.create(first_name="One", last_name="C", org=org_a)
        two = Contact.objects.create(first_name="Two", last_name="C", org=org_a)

        payload = admin_client.get("/api/accounts/").json()

        ids = {str(row["id"]) for row in payload["contacts"]}
        assert {str(one.id), str(two.id)} <= ids
