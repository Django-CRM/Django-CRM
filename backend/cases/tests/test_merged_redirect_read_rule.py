"""A merged ticket's redirect is behind the read rule (D45).

The ticket detail GET answered a merged duplicate's redirect before checking
read access, so any member got the duplicate's name and the id of the ticket
it was merged into.
"""

import pytest

from cases.models import Case


@pytest.fixture
def merged_pair(admin_user, org_a):
    target = Case.objects.create(
        name="Kept ticket",
        status="New",
        priority="Normal",
        org=org_a,
        created_by=admin_user,
    )
    duplicate = Case.objects.create(
        name="Secret duplicate",
        status="Closed",
        priority="Normal",
        org=org_a,
        created_by=admin_user,
        merged_into=target,
    )
    return duplicate, target


class TestMergedRedirect:
    def test_member_who_cannot_open_it_learns_nothing(self, user_client, merged_pair):
        duplicate, target = merged_pair
        response = user_client.get(f"/api/cases/{duplicate.id}/")
        assert response.status_code == 403
        body = response.content.decode()
        assert "Secret duplicate" not in body
        assert str(target.id) not in body

    def test_reader_gets_the_redirect(self, admin_client, merged_pair):
        duplicate, target = merged_pair
        response = admin_client.get(f"/api/cases/{duplicate.id}/")
        assert response.status_code == 200
        assert response.json()["redirect_to"] == str(target.id)
