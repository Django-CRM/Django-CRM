"""Shared pytest fixtures for Django CRM backend tests.

The definitions live in :mod:`common.testing` so that they ship inside the
wheel; see that module's docstring for why. This file re-exports them so pytest
discovers the fixtures at the repository root and so the twenty test modules
that already say ``from conftest import rls_org`` keep working.

Every name is listed explicitly rather than star-imported. A star import skips
names beginning with an underscore, which would silently drop
``_use_db`` and ``_restore_rls_context_after_each_request``: both are
``autouse``, so losing them does not fail loudly, it just stops granting
database access and stops restoring the RLS context after each request. That is
not a hypothetical. The enterprise suite re-exported this file with
``from community_conftest import *`` and has been missing both fixtures the
whole time.
"""

from common.testing import (  # noqa: F401
    _make_authenticated_client,
    _restore_rls_context_after_each_request,
    _use_db,
    admin_client,
    admin_profile,
    admin_user,
    clear_rls_context,
    org_a,
    org_b,
    org_b_client,
    profile_b,
    regular_user,
    restore_rls_context,
    rls_org,
    set_rls_context,
    unauthenticated_client,
    user_b,
    user_client,
    user_profile,
)
