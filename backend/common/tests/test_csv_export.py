"""The shared CSV writer: the formula guard, and the context it streams in."""

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone

from common import csv_export
from common.csv_export import csv_response, safe_cell


@pytest.mark.parametrize(
    "raw, cell",
    [
        ("=1+1", "'=1+1"),
        ("+44 20 7946 0000", "'+44 20 7946 0000"),
        ("-2", "'-2"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\t=cmd", "'\t=cmd"),
        ("\r=cmd", "'\r=cmd"),
        (
            ' =HYPERLINK("https://evil.example")',
            '\' =HYPERLINK("https://evil.example")',
        ),
        ("  +1", "'  +1"),
        ("\n-2", "'\n-2"),
        ("\xa0@SUM(A1)", "'\xa0@SUM(A1)"),
        (" Acme", " Acme"),
        ("Acme = best", "Acme = best"),
        ("", ""),
        (None, ""),
        (Decimal("-5.00"), Decimal("-5.00")),
        (-3, -3),
    ],
)
def test_safe_cell(raw, cell):
    assert safe_cell(raw) == cell


def test_stream_runs_in_the_orgs_rls_context_and_day(monkeypatch):
    """The body streams after the middleware has cleared both, so the
    response has to put them back for as long as it runs, then clear them."""
    calls = []
    monkeypatch.setattr(
        csv_export, "set_rls_context", lambda org_id: calls.append(("set", org_id))
    )
    monkeypatch.setattr(
        csv_export, "clear_rls_context", lambda: calls.append(("clear",))
    )
    org = SimpleNamespace(id=uuid4(), timezone="Asia/Kolkata")

    def rows():
        calls.append(("row", str(timezone.get_current_timezone())))
        yield ["a"]

    response = csv_response(rows(), "x.csv", org)
    assert calls == []  # nothing runs until the body is read
    body = b"".join(response.streaming_content).decode("utf-8")

    assert body == chr(0xFEFF) + "a\r\n"
    assert calls == [("set", org.id), ("row", "Asia/Kolkata"), ("clear",)]
    assert response["Content-Disposition"] == 'attachment; filename="x.csv"'


def test_context_is_cleared_when_a_row_raises(monkeypatch):
    calls = []
    monkeypatch.setattr(
        csv_export, "set_rls_context", lambda org_id: calls.append("set")
    )
    monkeypatch.setattr(csv_export, "clear_rls_context", lambda: calls.append("clear"))

    def rows():
        yield ["ok"]
        raise RuntimeError("boom")

    response = csv_response(
        rows(), "x.csv", SimpleNamespace(id=uuid4(), timezone="UTC")
    )
    with pytest.raises(RuntimeError):
        b"".join(response.streaming_content)
    assert calls == ["set", "clear"]


EXPORTS = {
    "leads": "/api/leads/export/",
    "contacts": "/api/contacts/export/",
    "accounts": "/api/accounts/export/",
    "opportunities": "/api/opportunities/export/",
    "cases": "/api/cases/export/",
    "invoices": "/api/invoices/export/",
}


@pytest.mark.parametrize("resource", sorted(EXPORTS))
def test_a_token_exports_exactly_what_it_may_list(admin_profile, resource):
    """Each export sits under its module's root, so a personal access token
    needs the same `<module>:read` scope for it as for the list, and a token
    scoped to another module is refused both."""
    from rest_framework.test import APIClient

    from common.models import PersonalAccessToken

    def client(scopes):
        raw, _ = PersonalAccessToken.generate(
            profile=admin_profile, name=f"t-{scopes}", scopes=scopes
        )
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        return c

    allowed, refused = client([f"{resource}:read"]), client(["tags:read"])
    url = EXPORTS[resource]
    list_url = url.removesuffix("export/")

    assert allowed.get(url).status_code == 200
    assert allowed.get(list_url).status_code == 200
    assert refused.get(url).status_code == 403
    assert refused.get(list_url).status_code == 403
