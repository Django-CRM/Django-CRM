"""
Tests for common/audit_log.py - AuditLogger methods not yet covered.

Covers:
- _get_request_info with X-Forwarded-For header
- logout
- cross_org_attempt
- api_key_used
- api_key_invalid
- membership_revoked
- suspicious_activity

Run with: pytest common/tests/test_audit_log.py -v
"""

import pytest
from django.test import RequestFactory

from common.audit_log import AuditLogger, SecurityAuditLog


@pytest.mark.django_db
class TestAuditLoggerMethods:
    """Test AuditLogger methods that lack coverage."""

    def setup_method(self):
        self.logger = AuditLogger()
        self.factory = RequestFactory()

    def test_get_request_info_with_forwarded_for(self):
        """The first X-Forwarded-For entry is the caller's to write, so it is
        not recorded. With no proxy configured the socket peer is."""
        request = self.factory.get(
            "/api/test/",
            HTTP_X_FORWARDED_FOR="203.0.113.50, 70.41.3.18, 150.172.238.178",
            HTTP_USER_AGENT="TestAgent/1.0",
            REMOTE_ADDR="192.0.2.10",
        )
        info = self.logger._get_request_info(request)
        assert info["ip_address"] == "192.0.2.10"
        assert info["user_agent"] == "TestAgent/1.0"
        assert info["request_path"] == "/api/test/"
        assert info["request_method"] == "GET"

    def test_get_request_info_records_the_relayed_visitor(self, settings):
        """A sign-in relayed by the web app records the visitor's address and
        browser, not the web server's address and HTTP client."""
        secret = "r" * 48
        settings.RELAY_SECRET = secret
        request = self.factory.post(
            "/api/auth/magic-link/verify/",
            HTTP_USER_AGENT="axios/1.20.0",
            HTTP_X_BOTTLECRM_RELAY_SECRET=secret,
            HTTP_X_BOTTLECRM_CLIENT_IP="203.0.113.50",
            HTTP_X_BOTTLECRM_USER_AGENT="Mozilla/5.0 (relayed)",
            REMOTE_ADDR="13.235.35.197",
        )
        info = self.logger._get_request_info(request)
        assert info["ip_address"] == "203.0.113.50"
        assert info["user_agent"] == "Mozilla/5.0 (relayed)"

    def test_get_request_info_none_request(self):
        """None request should return empty dict."""
        info = self.logger._get_request_info(None)
        assert info == {}

    def test_logout(self, admin_user, org_a):
        request = self.factory.post("/api/auth/logout/")
        self.logger.logout(admin_user, org_a, request)
        log = SecurityAuditLog.objects.filter(event_type="LOGOUT").first()
        assert log is not None
        assert log.user == admin_user
        assert log.org == org_a
        assert "logged out" in log.description

    def test_cross_org_attempt(self, admin_user, org_a, org_b):
        request = self.factory.get("/api/accounts/")
        self.logger.cross_org_attempt(admin_user, org_a, org_b, "Account:123", request)
        log = SecurityAuditLog.objects.filter(event_type="CROSS_ORG_ATTEMPT").first()
        assert log is not None
        assert log.success is False
        assert str(org_b.id) in log.metadata["target_org_id"]

    def test_api_key_used(self, org_a):
        request = self.factory.get("/api/leads/")
        self.logger.api_key_used(org_a, "/api/leads/", request)
        log = SecurityAuditLog.objects.filter(event_type="API_KEY_USED").first()
        assert log is not None
        assert log.org == org_a
        assert log.metadata["endpoint"] == "/api/leads/"

    def test_api_key_invalid(self):
        request = self.factory.get("/api/leads/")
        self.logger.api_key_invalid("abc123", request)
        log = SecurityAuditLog.objects.filter(event_type="API_KEY_INVALID").first()
        assert log is not None
        assert log.success is False
        assert "abc123" in log.metadata["api_key_prefix"]

    def test_membership_revoked(self, admin_user, org_a, regular_user):
        request = self.factory.post("/api/profiles/revoke/")
        self.logger.membership_revoked(
            admin_user, org_a, revoked_by=regular_user, request=request
        )
        log = SecurityAuditLog.objects.filter(event_type="MEMBERSHIP_REVOKED").first()
        assert log is not None
        assert log.user == admin_user
        assert log.org == org_a
        assert str(regular_user.id) in log.metadata["revoked_by"]

    def test_membership_revoked_by_system(self, admin_user, org_a):
        """When revoked_by is None, description should say 'system'."""
        self.logger.membership_revoked(admin_user, org_a)
        log = SecurityAuditLog.objects.filter(event_type="MEMBERSHIP_REVOKED").first()
        assert log is not None
        assert "system" in log.description

    def test_suspicious_activity(self, admin_user, org_a):
        request = self.factory.get("/api/accounts/")
        self.logger.suspicious_activity(
            admin_user, org_a, "BRUTE_FORCE", "Multiple failed logins", request
        )
        log = SecurityAuditLog.objects.filter(event_type="SUSPICIOUS_ACTIVITY").first()
        assert log is not None
        assert log.success is False
        assert log.metadata["activity_type"] == "BRUTE_FORCE"
        assert log.metadata["details"] == "Multiple failed logins"
