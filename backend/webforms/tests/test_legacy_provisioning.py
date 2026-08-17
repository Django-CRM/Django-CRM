"""Every legacy API key gets a backing web form, whoever made it.

`CreateLeadFromSite` writes leads through `webforms.service.submit_form` now,
which needs a `WebForm` describing what the key collects. Two ways a key can
end up without one, and both used to answer 409 forever:

* the backfill migration ran `APISettings.objects.all()` with no RLS context.
  `apiSettings` carries `FORCE ROW LEVEL SECURITY` and the policy returns no
  rows when `app.current_org` is empty, so on a correctly configured
  (non-superuser) production role the migration saw nothing and created
  nothing. It looked like it worked. The dev database runs as `postgres`, a
  superuser, which bypasses RLS entirely, and the suite runs on SQLite, so
  neither environment could show it.
* nothing outside that one-time migration ever set `legacy_api_setting`, so
  every key minted afterwards through the settings screen had no form at all.

`webforms.legacy.ensure_web_form` closes both: the key provisions its form on
first use, whatever created the key.
"""

import importlib
from types import SimpleNamespace

import pytest
from django.apps import apps as global_apps
from django.db import connection

from common.models import APISettings, Tags
from webforms.legacy import ensure_web_form
from webforms.models import WebForm

SITE_URL = "/api/leads/create-from-site/"
SETTINGS_URL = "/api/api-settings/"


@pytest.fixture
def api_setting(org_a, admin_user):
    return APISettings.objects.create(
        title="Marketing site",
        website="https://example.com",
        org=org_a,
        created_by=admin_user,
    )


@pytest.mark.django_db
class TestKeysMintedAfterTheMigration:
    def test_a_key_created_through_the_settings_api_captures_a_lead(
        self, admin_client, org_a
    ):
        """The regression in full. Creating a key through the screen that
        exists for it, then posting to the endpoint it exists for, answered
        409 'not configured for lead capture'."""
        created = admin_client.post(
            SETTINGS_URL,
            {"title": "My site", "website": "https://example.com"},
            format="json",
        )
        assert created.status_code in (200, 201)
        setting = APISettings.objects.get(org=org_a)

        response = admin_client.post(
            SITE_URL,
            {"apikey": setting.apikey, "email": "visitor@example.com"},
            format="json",
        )
        assert response.status_code == 200, response.data

    def test_a_key_created_in_the_orm_captures_a_lead(self, admin_client, api_setting):
        response = admin_client.post(
            SITE_URL,
            {"apikey": api_setting.apikey, "email": "orm@example.com"},
            format="json",
        )
        assert response.status_code == 200, response.data


@pytest.mark.django_db
class TestEnsureWebForm:
    def test_it_mirrors_the_keys_configuration(self, org_a, api_setting, user_profile):
        """The key's own recipients and tags, not `created_by` and nothing.
        Reading neither is what made the old endpoint notify the wrong person
        and drop the tags."""
        api_setting.lead_assigned_to.add(user_profile)
        tag = Tags.objects.create(name="Inbound", org=org_a)
        api_setting.tags.add(tag)

        form = ensure_web_form(api_setting)

        assert form.org == org_a
        assert form.name == "Marketing site"
        assert form.is_published is True
        assert form.assign_to == user_profile
        assert list(form.notify_profiles.all()) == [user_profile]
        assert list(form.tags.all()) == [tag]
        # A URL is not a LEAD_SOURCE member, and assigning one is what made
        # those leads invisible to every source filter.
        assert form.lead_source == "other"

    def test_it_collects_what_the_legacy_endpoint_always_read(self, api_setting):
        form = ensure_web_form(api_setting)
        assert [f.lead_field for f in form.fields.order_by("order")] == [
            "salutation",
            "first_name",
            "last_name",
            "email",
            "phone",
            "description",
        ]
        assert form.fields.get(lead_field="email").is_required is True

    def test_it_is_idempotent(self, api_setting):
        first = ensure_web_form(api_setting)
        second = ensure_web_form(api_setting)
        assert first.id == second.id
        assert WebForm.objects.count() == 1

    def test_it_does_not_republish_a_form_an_admin_turned_off(self, api_setting):
        """Once the form exists it belongs to the admin, not to this. Flipping
        it back on at every submission would make unpublish impossible."""
        form = ensure_web_form(api_setting)
        form.is_published = False
        form.save(update_fields=["is_published"])

        assert ensure_web_form(api_setting).is_published is False

    def test_a_key_with_no_recipients_still_gets_a_form(self, api_setting):
        """`assign_to` is nullable and the fallback is a Profile lookup that
        can legitimately find nothing. Returning None here rather than raising
        is what keeps a half-configured key working."""
        form = ensure_web_form(api_setting)
        assert form.assign_to is None or form.assign_to.org_id == api_setting.org_id


@pytest.mark.django_db
class TestBackfillCoversEveryOrg:
    """The migration's own function, called directly.

    It used to lean on one unscoped `APISettings.objects.all()`, which is the
    query RLS hides. It now walks orgs and filters by each one explicitly, so
    the rows it acts on are chosen by the query rather than by whatever the
    ambient context happens to expose.
    """

    def _run(self):
        # `importlib` because the module name starts with a digit. The real
        # models stand in for the historical ones, which is sound while
        # webforms is still at 0002 and is why `backfill` takes its models as
        # arguments rather than importing them.
        module = importlib.import_module(
            "webforms.migrations.0002_backfill_legacy_api_settings"
        )
        # A stand-in rather than `connection.schema_editor()`: the real one
        # refuses to open inside a test's transaction on SQLite, and `backfill`
        # reads nothing off it but `.connection`.
        module.backfill(global_apps, SimpleNamespace(connection=connection))

    def test_it_reaches_orgs_other_than_the_ambient_one(self, org_a, org_b, admin_user):
        for org in (org_a, org_b):
            APISettings.objects.create(
                title=f"Key for {org.name}",
                website="https://example.com",
                org=org,
                created_by=admin_user,
            )
        WebForm.objects.all().delete()

        self._run()

        assert WebForm.objects.filter(org=org_a).count() == 1
        assert WebForm.objects.filter(org=org_b).count() == 1

    def test_it_is_idempotent(self, org_a, api_setting):
        self._run()
        self._run()
        assert WebForm.objects.filter(legacy_api_setting=api_setting).count() == 1
