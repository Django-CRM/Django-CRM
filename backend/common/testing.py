"""Shared pytest fixtures and RLS helpers for tests against Django CRM.

Authenticated API clients, test organizations, users and profiles, plus the
helpers that drive the PostgreSQL ``app.current_org`` session variable.

This lives inside the ``common`` app rather than in the repository-root
``conftest.py`` so that it ships in the wheel. Anything defined only in that
conftest is unreachable from an installed copy: it sits outside every package,
so hatchling does not collect it, and a downstream test suite could only get at
it by pointing at a source checkout on disk. The enterprise suite did exactly
that, loading the file through a symlink by absolute path, which is the last
thing that tied the two repositories together at runtime.

The root ``conftest.py`` now re-exports every name below, so
``from conftest import rls_org`` keeps working unchanged in this repository.
Downstream consumers should import from ``common.testing`` instead.
"""

import contextlib

import pytest
from django.core.signals import request_finished
from django.db import connection
from rest_framework.test import APIClient

from common.models import Org, Profile, User
from common.serializer import OrgAwareRefreshToken

# The org a test's bare ORM calls act as. Tracked in a mutable holder rather
# than passed around because it has to be readable from the `request_finished`
# receiver below, which takes no test-specific arguments.
#
# On SQLite this whole mechanism is inert: RLS is a PostgreSQL feature, the
# session variable does not exist, and every helper here returns early.
_ambient_org = {"id": None}


def _apply_rls_context(org_id):
    """Set ``app.current_org`` without the statement counting as a query.

    Two details, both learned by breaking the suite:

    * It goes through the raw DBAPI cursor rather than ``connection.cursor()``,
      which would wrap it in Django's instrumentation. This runs from a
      ``request_finished`` receiver that fires inside the block
      ``django_assert_num_queries`` is measuring, and the harness putting the
      test's own context back is not a query the view performed.
    * It is skipped once the transaction is broken. Several tests deliberately
      provoke an ``IntegrityError`` to prove a unique constraint bites; after
      one of those, any further statement raises ``TransactionManagementError``,
      and running this in teardown turned each of those passes into an error.
    """
    if connection.vendor != "postgresql":
        return
    if connection.needs_rollback:
        return
    connection.ensure_connection()
    with connection.connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.current_org', %s, false)", [str(org_id or "")]
        )


def set_rls_context(org):
    """Make ``org`` the org this test's direct ORM calls read and write as.

    Needed because ``organization`` and ``profile`` are not org-scoped, but the
    other 58 tables are: under a role that RLS actually binds, a bare
    ``Lead.objects.create(org=org_a)`` with no context set is refused by the
    insert-check policy, and a bare read matches nothing.
    """
    _ambient_org["id"] = org.id
    _apply_rls_context(org.id)


def clear_rls_context():
    """Clear the PostgreSQL RLS session variable."""
    _ambient_org["id"] = None
    _apply_rls_context(None)


def restore_rls_context():
    """Re-apply the ambient context after something else cleared it.

    The Celery tasks that walk every org set ``app.current_org`` per org and
    clear it on the way out, which is right: a worker has no middleware and runs
    on a pooled connection, so leaving the last tenant's id behind would hand it
    to the next borrower. A test that calls such a task directly and then reads
    its own rows needs the context put back, the same way
    ``_restore_rls_context_after_each_request`` does it for an HTTP request.
    """
    _apply_rls_context(_ambient_org["id"])


@contextlib.contextmanager
def rls_org(org):
    """Run a block as ``org``, then restore the ambient context.

    For the cross-tenant tests, which have to plant a row in the *other* org
    before proving it stays invisible. The insert-check policy compares against
    the session variable, so seeding org B's row requires being org B for the
    length of that write and no longer.
    """
    previous = _ambient_org["id"]
    set_rls_context(org)
    try:
        yield
    finally:
        _ambient_org["id"] = previous
        _apply_rls_context(previous)


@pytest.fixture(autouse=True)
def _use_db(db):
    """Ensure all tests have database access."""


@pytest.fixture(autouse=True)
def _restore_rls_context_after_each_request(db):
    """Put the ambient context back once a test client request has finished.

    Takes ``db`` so pytest finalizes this fixture *before* the database one.
    Without that, the teardown below runs after pytest-django has revoked
    database access and every test in the run reports a teardown error.


    ``RLSContextMiddleware`` sets ``app.current_org`` from the JWT for the life
    of a request and clears it in a ``finally``, which is correct in production:
    the variable is session-scoped, connections are pooled, and a leftover value
    is how one tenant's context leaks into the next request. It also means that
    in a test, every ``client.get(...)`` wipes whatever the test set up, so the
    familiar "call the endpoint, then assert against the ORM" shape fails on the
    assertion rather than the behaviour under test.

    Restoring afterwards is not a hole in the enforcement: the request itself
    still ran under exactly the context the middleware derived, so any missing
    org filter still shows up. This only affects the test's own statements
    before and after.
    """
    if connection.vendor != "postgresql":
        yield
        return

    def _restore(**kwargs):
        if _ambient_org["id"] is not None:
            _apply_rls_context(_ambient_org["id"])

    request_finished.connect(_restore)
    try:
        yield
    finally:
        request_finished.disconnect(_restore)
        had_context = _ambient_org["id"] is not None
        _ambient_org["id"] = None
        if had_context:
            try:
                _apply_rls_context(None)
            except RuntimeError:
                # A `django.test.TestCase` re-blocks database access in its own
                # teardown, which for those tests runs before this fixture's.
                # The transaction that carried the context is already rolled
                # back by then, so there is nothing left to clear.
                pass


@pytest.fixture
def org_a():
    """The org a test acts as by default.

    Creating it also makes it the ambient RLS context, so a test that says
    ``Lead.objects.create(org=org_a)`` needs no further ceremony. ``org_b``
    deliberately does not do this: it exists to be the *other* tenant, and
    silently repointing the context at it would turn cross-org assertions into
    same-org ones.
    """
    org = Org.objects.create(name="Test Organization A")
    set_rls_context(org)
    return org


@pytest.fixture
def org_b():
    return Org.objects.create(name="Test Organization B")


@pytest.fixture
def admin_user():
    return User.objects.create_user(email="admin@test.com", password="testpass123")


@pytest.fixture
def regular_user():
    return User.objects.create_user(email="user@test.com", password="testpass123")


@pytest.fixture
def user_b():
    return User.objects.create_user(email="userb@test.com", password="testpass123")


@pytest.fixture
def admin_profile(admin_user, org_a):
    return Profile.objects.create(
        user=admin_user, org=org_a, role="ADMIN", is_active=True
    )


@pytest.fixture
def user_profile(regular_user, org_a):
    return Profile.objects.create(
        user=regular_user, org=org_a, role="USER", is_active=True
    )


@pytest.fixture
def profile_b(user_b, org_b):
    return Profile.objects.create(user=user_b, org=org_b, role="ADMIN", is_active=True)


def _make_authenticated_client(user, org, profile):
    """Create an APIClient with JWT auth for the given user/org."""
    client = APIClient()
    token = OrgAwareRefreshToken.for_user_and_org(user, org, profile)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


@pytest.fixture
def admin_client(admin_user, org_a, admin_profile):
    return _make_authenticated_client(admin_user, org_a, admin_profile)


@pytest.fixture
def user_client(regular_user, org_a, user_profile):
    return _make_authenticated_client(regular_user, org_a, user_profile)


@pytest.fixture
def org_b_client(user_b, org_b, profile_b):
    return _make_authenticated_client(user_b, org_b, profile_b)


@pytest.fixture
def unauthenticated_client():
    return APIClient()
