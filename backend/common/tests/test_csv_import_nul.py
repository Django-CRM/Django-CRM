"""A NUL byte in an import CSV is a header_error, not a Postgres 500.

Python's csv module accepts NUL since 3.11 and Postgres rejects it in any
string parameter. SQLite stores it happily, so an endpoint test would pass on
the default test database; these call the parsers directly instead.
"""

import pytest

from cases.services.csv_import import parse_and_validate as parse_tickets
from contacts.services.csv_import import parse_and_validate as parse_contacts

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "parse, csv_bytes",
    [
        (parse_contacts, b"first_name,last_name,email\nAda,Love\x00lace,a@x.com\n"),
        (parse_tickets, b"name,status,priority\nBro\x00ken,New,High\n"),
    ],
    ids=["contacts", "tickets"],
)
def test_nul_character_is_a_header_error(parse, csv_bytes, org_a, admin_profile):
    result = parse(csv_bytes, org_a, admin_profile)
    assert result.valid == []
    assert "NUL" in (result.header_error or "")


@pytest.mark.parametrize(
    "parse, csv_bytes",
    [
        (parse_contacts, b"first_name,last_name,email\nAda,Lovelace,a@x.com\n"),
        (parse_tickets, b"name,status,priority\nBroken,New,High\n"),
    ],
    ids=["contacts", "tickets"],
)
def test_clean_file_still_parses(parse, csv_bytes, org_a, admin_profile):
    result = parse(csv_bytes, org_a, admin_profile)
    assert result.header_error is None
    assert len(result.valid) == 1
