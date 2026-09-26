"""A tag's slug keeps non-ASCII letters (tracker D10).

`Tags.save()` used to store Django's ASCII `slugify(name)`, which mapped every
Japanese, Cyrillic, Arabic or Hindi name to "" and mixed names to their ASCII
remainder ("日本 2" and "中国 2" were both "2"). (slug, org) is unique, so an
org could hold one such tag at most and the second was refused as a duplicate
of the first, or reactivated it. `Tags.slug_for` is now the one owner of the
rule, and `Tags.name_error` the one owner of what a storable name is.
"""

import importlib
import uuid
from types import SimpleNamespace

import pytest
from django.apps import apps as global_apps
from django.db import IntegrityError, connection, transaction
from rest_framework import status

from common.models import Tags
from common.packs.applier import _apply_tags, _Report
from common.packs.schema import PackValidationError, validate_manifest
from common.utils import get_or_create_tags

URL = "/api/tags/"


class TestSlugFor:
    @pytest.mark.parametrize(
        "name, slug",
        [
            ("日本", "日本"),
            ("Привет мир", "привет-мир"),
            ("مرحبا", "مرحبا"),
            ("日本 2", "日本-2"),
            ("VIP Customer", "vip-customer"),
            ("  Trade   Show ", "trade-show"),
            ("ﬀ", "ff"),
            ("🎉", ""),
            ("!!!", ""),
        ],
    )
    def test_slug(self, name, slug):
        assert Tags.slug_for(name) == slug

    def test_mixed_names_no_longer_share_their_ascii_remainder(self):
        assert Tags.slug_for("日本 2") != Tags.slug_for("中国 2")

    def test_devanagari_vowel_signs_are_kept(self):
        # slugify(allow_unicode=True) drops combining marks, which folds all
        # three of these into "कल".
        slugs = {Tags.slug_for(n) for n in ("किला", "कल", "काल")}
        assert len(slugs) == 3

    def test_a_mark_on_an_emoji_does_not_make_a_slug(self):
        # "❤️" is U+2764 plus the variation selector U+FE0F, a combining mark.
        assert Tags.slug_for("❤️") == ""
        assert Tags.slug_for("a❤️") == "a"

    def test_composed_and_decomposed_forms_are_one_tag(self):
        assert Tags.slug_for("Caf\u00e9") == Tags.slug_for("Cafe\u0301")

    def test_accented_and_plain_latin_are_now_distinct(self):
        # The behaviour change for Latin names: names that differ only in an
        # accent or another non-ASCII letter collided before and no longer do.
        assert Tags.slug_for("Café") != Tags.slug_for("Cafe")


class TestNameError:
    @pytest.mark.parametrize("name", ["日本", "VIP", "ab" * 25])
    def test_storable_names_pass(self, name):
        assert Tags.name_error(name) is None

    @pytest.mark.parametrize("name", ["🎉", "!!!", "-_-"])
    def test_a_name_without_a_letter_or_digit_is_refused(self, name):
        assert "at least one letter or digit" in Tags.name_error(name)

    def test_a_name_over_the_column_is_refused(self):
        assert "at most 50" in Tags.name_error("a" * 51)

    def test_a_name_whose_slug_expands_past_the_column_is_refused(self):
        # 50 characters, but NFKC turns each ligature into two letters.
        assert "too long" in Tags.name_error("ﬀ" * 50)


class TestModel:
    def test_non_latin_tags_get_distinct_non_empty_slugs(self, org_a):
        tags = [
            Tags.objects.create(name=n, org=org_a)
            for n in ("日本", "中国", "Привет", "日本 2", "中国 2")
        ]
        slugs = [t.slug for t in tags]
        assert all(slugs)
        assert len(set(slugs)) == len(slugs)

    def test_same_non_latin_name_twice_in_one_org_is_refused(self, org_a):
        Tags.objects.create(name="日本", org=org_a)
        with pytest.raises(IntegrityError), transaction.atomic():
            Tags.objects.create(name="日本", org=org_a)

    def test_same_non_latin_name_in_two_orgs_is_fine(self, org_a, org_b):
        a = Tags.objects.create(name="日本", org=org_a)
        b = Tags.objects.create(name="日本", org=org_b)
        assert a.slug == b.slug == "日本"


class TestTagsApi:
    def test_second_non_latin_tag_is_created(self, admin_client, org_a):
        first = admin_client.post(URL, {"name": "日本"}, format="json")
        second = admin_client.post(URL, {"name": "中国"}, format="json")
        assert first.status_code == status.HTTP_201_CREATED, first.data
        assert second.status_code == status.HTTP_201_CREATED, second.data
        assert set(Tags.objects.filter(org=org_a).values_list("name", flat=True)) == {
            "日本",
            "中国",
        }

    def test_same_non_latin_name_is_still_a_duplicate(self, admin_client, org_a):
        Tags.objects.create(name="日本", org=org_a)
        response = admin_client.post(URL, {"name": "日本"}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "already exists" in response.data["errors"]["name"][0]
        assert Tags.objects.filter(org=org_a).count() == 1

    @pytest.mark.parametrize("name", ["🎉", "!!!", "ﬀ" * 50, "a." * 26])
    def test_unstorable_name_is_a_400(self, admin_client, org_a, name):
        response = admin_client.post(URL, {"name": name}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["errors"]["name"]
        assert not Tags.objects.filter(org=org_a).exists()

    def test_reactivation_revives_the_matching_tag_only(self, admin_client, org_a):
        archived = Tags.objects.create(name="日本", org=org_a, is_active=False)
        bystander = Tags.objects.create(name="中国", org=org_a, is_active=False)
        response = admin_client.post(URL, {"name": "日本"}, format="json")
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["tag"]["id"] == str(archived.id)
        archived.refresh_from_db()
        bystander.refresh_from_db()
        assert archived.is_active is True
        assert bystander.is_active is False

    def test_rename_to_a_different_non_latin_name_is_allowed(self, admin_client, org_a):
        Tags.objects.create(name="日本", org=org_a)
        tag = Tags.objects.create(name="Asia", org=org_a)
        response = admin_client.put(f"{URL}{tag.pk}/", {"name": "中国"}, format="json")
        assert response.status_code == status.HTTP_200_OK, response.data
        tag.refresh_from_db()
        assert (tag.name, tag.slug) == ("中国", "中国")

    def test_rename_onto_an_existing_non_latin_name_is_refused(
        self, admin_client, org_a
    ):
        Tags.objects.create(name="日本", org=org_a)
        tag = Tags.objects.create(name="Asia", org=org_a)
        response = admin_client.put(f"{URL}{tag.pk}/", {"name": "日本"}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        tag.refresh_from_db()
        assert tag.name == "Asia"

    def test_rename_to_an_unstorable_name_is_a_400(self, admin_client, org_a):
        tag = Tags.objects.create(name="Asia", org=org_a)
        response = admin_client.put(f"{URL}{tag.pk}/", {"name": "🎉"}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        tag.refresh_from_db()
        assert tag.name == "Asia"


class TestPacks:
    def test_pack_tags_with_non_latin_names_are_created_then_skipped(self, org_a):
        pack = {"tags": [{"name": "日本"}, {"name": "中国"}]}
        first = _Report()
        _apply_tags(org_a, pack, first)
        assert [c["name"] for c in first.created] == ["日本", "中国"]

        again = _Report()
        _apply_tags(org_a, pack, again)
        assert again.created == []
        assert [s["name"] for s in again.skipped] == ["日本", "中国"]

    def test_manifest_with_an_unstorable_tag_name_is_rejected(self):
        raw = {"id": "demo", "version": 1, "name": "Demo", "tags": [{"name": "🎉"}]}
        with pytest.raises(PackValidationError, match="letter or digit"):
            validate_manifest(raw)


class TestGetOrCreateTags:
    def test_finds_an_existing_tag_by_its_slug(self, org_a):
        existing = Tags.objects.create(name="Big Deal", org=org_a)
        assert get_or_create_tags(["big deal"], org_a) == [existing]

    def test_non_latin_names_become_separate_tags(self, org_a):
        tags = get_or_create_tags(["日本", "中国"], org_a)
        assert len({t.id for t in tags}) == 2

    def test_unstorable_names_are_skipped(self, org_a):
        assert get_or_create_tags(["🎉", "ﬀ" * 50], org_a) == []
        assert not Tags.objects.filter(org=org_a).exists()


class TestRecomputeMigration:
    """`common/0042`'s own function, called directly, as in webforms/0002's
    tests. The real models stand in for the historical ones, which is sound
    while `Tags` has the same fields as at 0042."""

    def _run(self):
        module = importlib.import_module("common.migrations.0042_recompute_tag_slugs")
        module.recompute_tag_slugs(global_apps, SimpleNamespace(connection=connection))

    def _with_slug(self, org, name, slug):
        # save() recomputes the slug, so store the old one with update(). The
        # placeholder must fit `name`'s 50 characters: Postgres enforces the
        # width and SQLite does not, so a longer one only fails on Postgres.
        tag = Tags.objects.create(name=f"placeholder {uuid.uuid4().hex}", org=org)
        Tags.objects.filter(pk=tag.pk).update(name=name, slug=slug)
        return tag

    def test_old_ascii_slugs_are_recomputed_in_every_org(self, org_a, org_b):
        rows = [
            (org_a, "日本", ""),
            (org_a, "日本 2", "2"),
            (org_a, "Café", "cafe"),
            (org_a, "VIP", "vip"),
            (org_b, "Привет", ""),
        ]
        tags = [self._with_slug(*row) for row in rows]

        self._run()

        got = [Tags.objects.get(pk=t.pk).slug for t in tags]
        assert got == ["日本", "日本-2", "café", "vip", "привет"]

    def test_a_clash_stops_the_migration_and_names_the_tags(self, org_a):
        # Two names that the new rule maps to one slug. Unreachable through
        # save(); written with update() to prove the check fires.
        self._with_slug(org_a, "a\u2028b", "ab")
        self._with_slug(org_a, "a b", "a-b")
        with pytest.raises(RuntimeError, match=str(org_a.id)) as exc:
            self._run()
        assert "'a b'" in str(exc.value)
        assert set(Tags.objects.values_list("slug", flat=True)) == {"ab", "a-b"}

    def test_a_slug_past_the_column_stops_the_migration(self, org_a):
        self._with_slug(org_a, "ﬀ" * 50, "f" * 50)
        with pytest.raises(RuntimeError, match="100-character slug"):
            self._run()
