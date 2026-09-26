"""Access control and list aggregates for opportunities.

Written while wiring the v2 pipeline pages to the real API. Driving the
endpoints (rather than reading them) turned up five defects, and each one has a
test here in both directions. A permission check that has never been shown to
return `True` is indistinguishable from one that cannot.

- `get`, `put`, `patch` and `post` compared `request.profile` (a Profile) to
  `opportunity.created_by` (a FK to User). Never equal, so the creator branch
  was dead and a non-admin was locked out of the deal they had created.
  `delete` used the correct comparison, so the same person could delete a deal
  they were forbidden to read.
- `users_mention` read `created_by.user.email`. `created_by` IS the user, so
  every non-admin assignee opening a deal got a 500.
- `post` (add comment) looked the deal up with `.get()`, so a missing id was a
  500 rather than a 404.
- `put` and `post` assigned `closed_by` and never saved it.
- Closing a deal required neither an amount nor a close date through the API,
  though `Opportunity.clean()` declares both. DRF never calls `clean()`.
"""

from decimal import Decimal

import pytest
from django.db import connection
from rest_framework import status

from opportunity.models import DealStage, Opportunity

OPPORTUNITIES_LIST_URL = "/api/opportunities/"


def _detail_url(pk):
    return f"/api/opportunities/{pk}/"


def _set_rls(org):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_org', %s, false)", [str(org.id)])


def _created_by(opportunity, user):
    """Stage a creator.

    `BaseModel.save()` overwrites `created_by` from the crum thread-local and
    ignores whatever the caller passed, so `objects.create(created_by=...)`
    silently produces a row with `created_by = None` in a test with no request
    in flight. `.update()` skips `save()` and is the only way to set it.
    """
    Opportunity.objects.filter(pk=opportunity.pk).update(created_by=user)
    opportunity.refresh_from_db()
    return opportunity


def _deal(org, **kwargs):
    _set_rls(org)
    kwargs.setdefault("name", "Deal")
    kwargs.setdefault("stage", "QUALIFICATION")
    return Opportunity.objects.create(org=org, **kwargs)


@pytest.mark.django_db
class TestDealDetailAccess:
    """Who may read one deal."""

    def test_admin_reads_any_deal(self, admin_client, org_a):
        deal = _deal(org_a, name="Someone Elses")
        response = admin_client.get(_detail_url(deal.pk))
        assert response.status_code == status.HTTP_200_OK

    def test_assigned_non_admin_reads_it(
        self, user_client, user_profile, admin_user, org_a
    ):
        """The regression test for the 500.

        This is the ordinary case, a rep opening a deal an admin created and
        assigned to them, and it raised AttributeError on `created_by.user`.
        `_created_by` matters: with `created_by` left null the buggy branch is
        skipped and the test passes against broken code.
        """
        deal = _created_by(_deal(org_a, name="Assigned To Me"), admin_user)
        deal.assigned_to.add(user_profile)
        response = user_client.get(_detail_url(deal.pk))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["users_mention"] == [{"user__email": admin_user.email}]

    def test_creator_non_admin_reads_own_deal(self, user_client, user_profile, org_a):
        """The dead branch, proven live."""
        deal = _created_by(_deal(org_a, name="Mine"), user_profile.user)
        response = user_client.get(_detail_url(deal.pk))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["comment_permission"] is True

    def test_unrelated_non_admin_is_refused(
        self, user_client, user_profile, admin_user, org_a
    ):
        deal = _created_by(_deal(org_a, name="Not Mine"), admin_user)
        response = user_client.get(_detail_url(deal.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_other_org_is_not_found(self, org_b_client, org_a):
        """404, not 403. The reply must not confirm the id exists."""
        deal = _deal(org_a, name="Org A Only")
        response = org_b_client.get(_detail_url(deal.pk))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_deal_with_no_creator_still_renders(self, user_client, user_profile, org_a):
        """`created_by` is nullable. SET_NULL on user deletion."""
        deal = _deal(org_a, name="Orphan")
        deal.assigned_to.add(user_profile)
        response = user_client.get(_detail_url(deal.pk))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["users_mention"] == []


@pytest.mark.django_db
class TestDealWriteAccess:
    """Who may change one deal. Same rule as reading, tested separately
    because `put`, `patch` and `post` each carried their own copy of it."""

    def test_creator_non_admin_can_patch_own_deal(
        self, user_client, user_profile, org_a
    ):
        deal = _created_by(_deal(org_a, name="Editable"), user_profile.user)
        response = user_client.patch(
            _detail_url(deal.pk), {"stage": "PROPOSAL"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        deal.refresh_from_db()
        assert deal.stage == "PROPOSAL"

    def test_creator_non_admin_can_put_own_deal(self, user_client, user_profile, org_a):
        deal = _created_by(_deal(org_a, name="Puttable"), user_profile.user)
        response = user_client.put(
            _detail_url(deal.pk),
            {"name": "Puttable", "stage": "PROPOSAL"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_assigned_non_admin_can_patch(
        self, user_client, user_profile, admin_user, org_a
    ):
        deal = _created_by(_deal(org_a, name="Assigned Edit"), admin_user)
        deal.assigned_to.add(user_profile)
        response = user_client.patch(
            _detail_url(deal.pk), {"stage": "PROPOSAL"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK

    def test_unrelated_non_admin_cannot_patch(
        self, user_client, user_profile, admin_user, org_a
    ):
        deal = _created_by(_deal(org_a, name="Locked"), admin_user)
        response = user_client.patch(
            _detail_url(deal.pk), {"stage": "PROPOSAL"}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        deal.refresh_from_db()
        assert deal.stage == "QUALIFICATION"

    def test_unrelated_non_admin_cannot_assign_themselves(
        self, user_client, user_profile, admin_user, org_a
    ):
        """The escalation shape: granting yourself access to a deal you cannot
        currently see, by naming yourself in the body."""
        deal = _created_by(_deal(org_a, name="No Self Assign"), admin_user)
        response = user_client.patch(
            _detail_url(deal.pk),
            {"assigned_to": [str(user_profile.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert deal.assigned_to.count() == 0

    def test_comment_on_missing_deal_is_404(self, admin_client):
        response = admin_client.post(
            _detail_url("11111111-1111-1111-1111-111111111111"),
            {"comment": "Where did it go"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_comment_on_other_org_deal_is_404(self, org_b_client, org_a):
        deal = _deal(org_a, name="Org A Comment")
        response = org_b_client.post(
            _detail_url(deal.pk), {"comment": "Not yours"}, format="json"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestClosingADeal:
    """`Opportunity.clean()` requires a close date on any closed stage and an
    amount on Closed Won. DRF does not call `clean()`, so these are enforced in
    `OpportunityCreateSerializer.validate()`."""

    def test_patch_closed_won_without_amount_is_rejected(self, admin_client, org_a):
        deal = _deal(org_a, name="Winning")
        response = admin_client.patch(
            _detail_url(deal.pk),
            {"stage": "CLOSED_WON", "closed_on": "2026-02-01"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "amount" in response.data["errors"]
        deal.refresh_from_db()
        assert deal.stage == "QUALIFICATION"

    def test_patch_closed_lost_without_date_is_rejected(self, admin_client, org_a):
        deal = _deal(org_a, name="Losing")
        response = admin_client.patch(
            _detail_url(deal.pk), {"stage": "CLOSED_LOST"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "closed_on" in response.data["errors"]

    def test_patch_stage_only_uses_the_stored_amount(self, admin_client, org_a):
        """A PATCH that only moves the stage is judged against what is already
        on the record, not against the empty half of its own body."""
        deal = _deal(
            org_a, name="Already Priced", amount=Decimal("4000"), closed_on="2026-03-01"
        )
        response = admin_client.patch(
            _detail_url(deal.pk), {"stage": "CLOSED_WON"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        deal.refresh_from_db()
        assert deal.stage == "CLOSED_WON"

    def test_closing_records_who_closed_it_over_put(
        self, admin_client, admin_profile, org_a
    ):
        """`put` assigned `closed_by` and never saved."""
        deal = _deal(org_a, name="Put Closer")
        response = admin_client.put(
            _detail_url(deal.pk),
            {
                "name": "Put Closer",
                "stage": "CLOSED_WON",
                "amount": "1200.00",
                "closed_on": "2026-02-02",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        deal.refresh_from_db()
        assert deal.closed_by == admin_profile

    def test_creating_an_already_won_deal_records_the_closer(
        self, admin_client, admin_profile, org_a
    ):
        """Same missing save on create."""
        response = admin_client.post(
            OPPORTUNITIES_LIST_URL,
            {
                "name": "Born Won",
                "stage": "CLOSED_WON",
                "amount": "800.00",
                "closed_on": "2026-02-03",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        deal = Opportunity.objects.get(name="Born Won")
        assert deal.closed_by == admin_profile

    def test_open_stages_need_neither(self, admin_client, org_a):
        """The rule applies to closing, not to working."""
        response = admin_client.post(
            OPPORTUNITIES_LIST_URL,
            {"name": "Just Started", "stage": "PROSPECTING"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestDealListTotals:
    """`totals` describes the whole filtered queryset. The page it feeds shows
    ten rows; a header derived from those ten would change when you paginate."""

    def test_totals_cover_every_match_not_just_the_page(self, admin_client, org_a):
        for index in range(14):
            _deal(org_a, name=f"Bulk {index}", amount=Decimal("100"))
        response = admin_client.get(f"{OPPORTUNITIES_LIST_URL}?limit=5")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["opportunities"]) == 5
        assert response.data["totals"]["count"] == 14
        assert Decimal(response.data["totals"]["amount_sum"]) == Decimal("1400")

    def test_weighted_sum_discounts_by_probability(self, admin_client, org_a):
        # save() fills probability from STAGE_PROBABILITIES: PROPOSAL is 50.
        _deal(org_a, name="Half Likely", stage="PROPOSAL", amount=Decimal("1000"))
        response = admin_client.get(OPPORTUNITIES_LIST_URL)
        totals = response.data["totals"]
        assert Decimal(totals["amount_sum"]) == Decimal("1000")
        assert Decimal(totals["weighted_sum"]) == Decimal("500")

    def test_amounts_are_zero_not_null_when_nothing_is_priced(
        self, admin_client, org_a
    ):
        _deal(org_a, name="Unpriced")
        totals = admin_client.get(OPPORTUNITIES_LIST_URL).data["totals"]
        assert Decimal(totals["amount_sum"]) == Decimal("0")
        assert Decimal(totals["weighted_sum"]) == Decimal("0")

    def test_totals_exclude_other_orgs(self, admin_client, org_a, org_b):
        _deal(org_a, name="Ours", amount=Decimal("50"))
        _deal(org_b, name="Theirs", amount=Decimal("9999"))
        totals = admin_client.get(OPPORTUNITIES_LIST_URL).data["totals"]
        assert totals["count"] == 1
        assert Decimal(totals["amount_sum"]) == Decimal("50")

    def test_totals_respect_the_open_filter(self, admin_client, org_a):
        _deal(org_a, name="Working", amount=Decimal("100"))
        _deal(
            org_a,
            name="Done",
            stage="CLOSED_WON",
            amount=Decimal("900"),
            closed_on="2026-01-01",
        )
        totals = admin_client.get(f"{OPPORTUNITIES_LIST_URL}?open=true").data["totals"]
        assert totals["count"] == 1
        assert Decimal(totals["amount_sum"]) == Decimal("100")

    def test_totals_narrow_for_a_non_admin(
        self, user_client, user_profile, admin_user, org_a
    ):
        """A rep's header counts the rep's deals. The same subset the list
        shows them, since the queryset is already narrowed before totalling."""
        _created_by(_deal(org_a, name="Theirs", amount=Decimal("500")), admin_user)
        _created_by(_deal(org_a, name="Mine", amount=Decimal("300")), user_profile.user)
        totals = user_client.get(OPPORTUNITIES_LIST_URL).data["totals"]
        assert totals["count"] == 1
        assert Decimal(totals["amount_sum"]) == Decimal("300")

    def test_open_filter_excludes_closed_deals(self, admin_client, org_a):
        _deal(org_a, name="Open One")
        _deal(
            org_a,
            name="Won One",
            stage="CLOSED_WON",
            amount=Decimal("10"),
            closed_on="2026-01-01",
        )
        _deal(org_a, name="Lost One", stage="CLOSED_LOST", closed_on="2026-01-01")
        response = admin_client.get(f"{OPPORTUNITIES_LIST_URL}?open=true")
        names = {row["name"] for row in response.data["opportunities"]}
        assert names == {"Open One"}


def _money(rows, field):
    """``{currency: Decimal(field)}`` from a ``by_currency`` list."""
    return {r["currency"]: Decimal(r[field]) for r in rows}


@pytest.mark.django_db
class TestDealTotalsNeverAddCurrencies:
    """Each deal carries its own currency and there are no exchange rates.

    `amount_sum` and `weighted_sum` were one `Sum` each over every deal, so a
    deal in USD and one in EUR came back as one number and the pipeline header
    printed it in the org's currency. They now follow the invoice reports'
    convention: the plain figure when at most one currency is present, `None`
    when several, and `by_currency` either way.
    """

    def _totals(self, client):
        response = client.get(OPPORTUNITIES_LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        return response.json()["totals"]

    def test_one_currency_keeps_its_plain_figures(self, admin_client, org_a):
        _deal(org_a, stage="PROPOSAL", amount=Decimal("500"), currency="EUR")

        totals = self._totals(admin_client)

        assert Decimal(totals["amount_sum"]) == Decimal("500")
        assert Decimal(totals["weighted_sum"]) == Decimal("250")
        assert [r["currency"] for r in totals["by_currency"]] == ["EUR"]

    def test_two_currencies_are_never_summed(self, admin_client, org_a):
        _deal(org_a, stage="PROPOSAL", amount=Decimal("1000"), currency="USD")
        _deal(org_a, stage="PROPOSAL", amount=Decimal("300"), currency="EUR")
        _deal(org_a, name="Unpriced", currency="GBP")

        totals = self._totals(admin_client)

        assert totals["amount_sum"] is None
        assert totals["weighted_sum"] is None
        assert totals["count"] == 3
        assert _money(totals["by_currency"], "amount_sum") == {
            "EUR": Decimal("300"),
            "USD": Decimal("1000"),
        }
        assert _money(totals["by_currency"], "weighted_sum") == {
            "EUR": Decimal("150"),
            "USD": Decimal("500"),
        }

    def test_a_deal_with_no_currency_is_in_the_orgs_default(self, admin_client, org_a):
        """What the deal serializer assumes when it fills a blank currency."""
        org_a.default_currency = "INR"
        org_a.save()
        _deal(org_a, name="Blank", amount=Decimal("40"), currency="")
        _deal(org_a, name="Null", amount=Decimal("10"))
        _deal(org_a, name="Rupees", amount=Decimal("50"), currency="INR")

        totals = self._totals(admin_client)

        assert Decimal(totals["amount_sum"]) == Decimal("100")
        assert [r["currency"] for r in totals["by_currency"]] == ["INR"]

    def test_another_orgs_deal_adds_no_currency(self, admin_client, org_a, org_b):
        _deal(org_a, name="Ours", amount=Decimal("50"), currency="USD")
        _deal(org_b, name="Theirs", amount=Decimal("9999"), currency="EUR")

        totals = self._totals(admin_client)

        assert Decimal(totals["amount_sum"]) == Decimal("50")
        assert _money(totals["by_currency"], "amount_sum") == {"USD": Decimal("50")}

    def test_a_deal_with_two_assignees_is_counted_once(
        self, user_client, user_profile, admin_profile, org_a
    ):
        """The non-admin queryset joins `assigned_to`; grouping over that join
        would add a deal once per assignee."""
        deal = _deal(org_a, name="Shared", amount=Decimal("70"), currency="USD")
        deal.assigned_to.add(user_profile, admin_profile)

        totals = self._totals(user_client)

        assert totals["count"] == 1
        assert Decimal(totals["amount_sum"]) == Decimal("70")


@pytest.mark.django_db
class TestStalledCount:
    """`stalled_count` and a red aging pill have to mean the same thing. Both
    come from `stalled_filter()`."""

    def _aged(self, org, name, stage, days):
        from datetime import timedelta

        from django.utils import timezone

        deal = _deal(org, name=name, stage=stage)
        Opportunity.objects.filter(pk=deal.pk).update(
            stage_changed_at=timezone.now() - timedelta(days=days)
        )
        deal.refresh_from_db()
        return deal

    def test_counts_deals_past_the_red_line(self, admin_client, org_a):
        # PROPOSAL expects 10 days; red at 10 * 1.5 = 15.
        stale = self._aged(org_a, "Stale", "PROPOSAL", days=20)
        self._aged(org_a, "Fresh", "PROPOSAL", days=2)
        totals = admin_client.get(OPPORTUNITIES_LIST_URL).data["totals"]
        assert totals["stalled_count"] == 1
        assert stale.get_aging_status() == "red"

    def test_agrees_with_the_pill_on_the_row(self, admin_client, org_a):
        """The invariant that matters: the header number equals the number of
        red rows. If these ever diverge the header is lying about the list."""
        self._aged(org_a, "A", "PROPOSAL", days=20)
        self._aged(org_a, "B", "NEGOTIATION", days=40)
        self._aged(org_a, "C", "PROSPECTING", days=1)
        response = admin_client.get(OPPORTUNITIES_LIST_URL)
        reds = [
            row
            for row in response.data["opportunities"]
            if row["aging_status"] == "red"
        ]
        assert response.data["totals"]["stalled_count"] == len(reds) == 2

    def test_closed_deals_are_never_stalled(self, admin_client, org_a):
        deal = self._aged(org_a, "Ancient Win", "PROSPECTING", days=400)
        Opportunity.objects.filter(pk=deal.pk).update(
            stage="CLOSED_WON", closed_on="2026-01-01"
        )
        totals = admin_client.get(OPPORTUNITIES_LIST_URL).data["totals"]
        assert totals["stalled_count"] == 0

    def test_org_config_moves_the_line(self, admin_client, org_a):
        """A deal that is fine under the default becomes stalled when the org
        says that stage should take three days."""
        self._aged(org_a, "Borderline", "PROPOSAL", days=6)
        assert (
            admin_client.get(OPPORTUNITIES_LIST_URL).data["totals"]["stalled_count"]
            == 0
        )
        DealStage.objects.filter(org=org_a, code="PROPOSAL").update(expected_days=3)
        assert (
            admin_client.get(OPPORTUNITIES_LIST_URL).data["totals"]["stalled_count"]
            == 1
        )

    def test_deals_never_moved_are_not_counted(self, admin_client, org_a):
        """`stage_changed_at` is set on create, so this is defensive: a null
        would otherwise slip through the `__lte` comparison as a non-match."""
        deal = _deal(org_a, name="Never Moved", stage="PROPOSAL")
        Opportunity.objects.filter(pk=deal.pk).update(stage_changed_at=None)
        totals = admin_client.get(OPPORTUNITIES_LIST_URL).data["totals"]
        assert totals["stalled_count"] == 0
