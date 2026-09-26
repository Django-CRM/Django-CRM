"""Import tags go through `Tags.name_error` and `Tags.slug_for`.

(slug, org) is unique and the slug is capped at 50 characters. The ASCII slug
the importers used to check mapped every non-Latin name to "", so they refused
such tags outright. The Unicode slug gives each one its own key, so they now
import and are reused on a second import; a name with no letter or digit, or
one whose slug expands past 50 characters, is still a row error.
"""

import pytest

from cases.services.csv_import import commit_rows as commit_tickets
from cases.services.csv_import import parse_and_validate as parse_tickets
from common.models import Tags
from contacts.services.csv_import import commit_rows as commit_contacts
from contacts.services.csv_import import parse_and_validate as parse_contacts

pytestmark = pytest.mark.django_db


CONTACTS = b"first_name,last_name,email,tags\nAda,Lovelace,%s@x.com,%s\n"
TICKETS = b"name,status,priority,tags\nBroken %s,New,High,%s\n"
IMPORTERS = [
    (parse_contacts, commit_contacts, CONTACTS),
    (parse_tickets, commit_tickets, TICKETS),
]
IDS = ["contacts", "tickets"]


def _tag_errors(result):
    return [e for e in result.errors if e.field == "tags"]


@pytest.mark.parametrize("parse, commit, template", IMPORTERS, ids=IDS)
@pytest.mark.parametrize(
    "tag, message",
    [
        ("🎉".encode(), "at least one letter or digit"),
        (("ﬀ" * 50).encode(), "too long"),
    ],
    ids=["no-letter", "overlong-slug"],
)
def test_tag_without_a_storable_slug_is_a_row_error(
    parse, commit, template, tag, message, org_a, admin_profile
):
    result = parse(template % (b"a", tag), org_a, admin_profile)
    assert result.valid == []
    errors = _tag_errors(result)
    assert errors
    assert message in errors[0].message


@pytest.mark.parametrize("parse, commit, template", IMPORTERS, ids=IDS)
def test_ordinary_tag_is_valid(parse, commit, template, org_a, admin_profile):
    result = parse(template % (b"a", b"VIP Customer"), org_a, admin_profile)
    assert not result.errors
    assert result.valid[0].tag_names == ["VIP Customer"]


@pytest.mark.parametrize("parse, commit, template", IMPORTERS, ids=IDS)
def test_non_latin_tags_import_and_are_reused(
    parse, commit, template, org_a, admin_profile
):
    first = commit(template % (b"one", "日本;中国".encode()), org_a, admin_profile)
    second = commit(template % (b"two", "日本".encode()), org_a, admin_profile)
    assert first["error"] is False, first
    assert second["error"] is False, second
    tags = Tags.objects.filter(org=org_a)
    assert sorted(tags.values_list("slug", flat=True)) == sorted(["日本", "中国"])
