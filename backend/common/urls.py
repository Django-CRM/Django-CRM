from django.urls import path

from common.views.attachment_views import AttachmentDownloadView
from common.views.auth_views import (
    GoogleIdTokenView,
    GoogleOAuthCallbackView,
    LogoutView,
    MagicLinkRequestView,
    MagicLinkVerifyCodeView,
    MagicLinkVerifyView,
    MeView,
    OrgAwareTokenRefreshView,
    OrgSwitchView,
)
from common.views.custom_field_views import (
    CustomFieldDefinitionDetailView,
    CustomFieldDefinitionListCreateView,
)
from common.views.dashboard_views import ActivityListView, ApiHomeView, ApiTodayView
from common.views.document_views import (
    DocumentDetailView,
    DocumentDownloadView,
    DocumentListView,
)
from common.views.notification_views import (
    NotificationDetailView,
    NotificationListView,
    NotificationReadAllView,
    NotificationReadView,
)
from common.views.org_settings_views import (
    HelpCenterSettingsView,
    OrgSettingsView,
    TimezoneListView,
)
from common.views.organization_views import (
    OrgApiKeyView,
    OrgProfileCreateView,
    OrgUpdateView,
    ProfileDetailView,
    ProfileView,
)
from common.views.pack_views import PackApplyView, PackListView, PackSampleDataView
from common.views.pat_views import (
    OrgAccessTokenDetailView,
    OrgAccessTokenListView,
    PersonalAccessTokenDetailView,
    PersonalAccessTokenListCreateView,
)
from common.views.settings_views import DomainDetailView, DomainList
from common.views.tags_views import (
    TagsDetailView,
    TagsListView,
    TagsMergeView,
    TagsRestoreView,
)
from common.views.team_views import TeamsDetailView, TeamsListView
from common.views.user_views import (
    GetTeamsAndUsersView,
    UserDetailView,
    UsersListView,
    UserStatusView,
)

app_name = "api_common"


urlpatterns = [
    path("dashboard/", ApiHomeView.as_view()),
    path("dashboard/today/", ApiTodayView.as_view()),
    # JWT Authentication endpoints for SvelteKit integration
    path(
        "auth/refresh-token/",
        OrgAwareTokenRefreshView.as_view(),
        name="token_refresh",
    ),
    path("auth/me/", MeView.as_view(), name="me"),
    path("auth/profile/", ProfileDetailView.as_view(), name="profile_detail"),
    path("auth/switch-org/", OrgSwitchView.as_view(), name="switch_org"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    # Google OAuth callback with PKCE (secure implementation)
    path("auth/google/callback/", GoogleOAuthCallbackView.as_view()),
    # Google ID token auth for mobile apps
    path("auth/google/", GoogleIdTokenView.as_view(), name="google_id_token"),
    # Magic link (passwordless) authentication
    path(
        "auth/magic-link/request/",
        MagicLinkRequestView.as_view(),
        name="magic_link_request",
    ),
    path(
        "auth/magic-link/verify/",
        MagicLinkVerifyView.as_view(),
        name="magic_link_verify",
    ),
    path(
        "auth/magic-link/verify-code/",
        MagicLinkVerifyCodeView.as_view(),
        name="magic_link_verify_code",
    ),
    # Organization and profile management
    path("org/", OrgProfileCreateView.as_view()),
    path("org/settings/", OrgSettingsView.as_view(), name="org_settings"),
    # Static reference data, org-free by design: the first caller is a user
    # creating their first org and has no org claim yet.
    path("org/timezones/", TimezoneListView.as_view(), name="timezone_list"),
    # These literal org/… paths must precede org/<uid:pk>/ so they are not
    # captured as a pk (org/tokens/ would otherwise resolve to OrgUpdateView
    # with pk="tokens"). org/tokens/ is ADMIN-only token oversight, separate
    # from profile/tokens/ (self-scoped) so the self guard is never widened; an
    # admin sees and can revoke any token in their own org, a deactivated
    # colleague's included.
    path("org/api-key/", OrgApiKeyView.as_view(), name="org_api_key"),
    path(
        "org/help-center/",
        HelpCenterSettingsView.as_view(),
        name="help_center_settings",
    ),
    path("org/tokens/", OrgAccessTokenListView.as_view(), name="org_pat_list"),
    path(
        "org/tokens/<uuid:pk>/",
        OrgAccessTokenDetailView.as_view(),
        name="org_pat_detail",
    ),
    path("org/<uid:pk>/", OrgUpdateView.as_view()),
    path("profile/", ProfileView.as_view()),
    # Personal Access Tokens (REST API), a user manages ONLY their own
    path(
        "profile/tokens/",
        PersonalAccessTokenListCreateView.as_view(),
        name="pat_list_create",
    ),
    path(
        "profile/tokens/<uuid:pk>/",
        PersonalAccessTokenDetailView.as_view(),
        name="pat_detail",
    ),
    # User management
    path("users/get-teams-and-users/", GetTeamsAndUsersView.as_view()),
    path("users/", UsersListView.as_view()),
    path("user/<uid:pk>/", UserDetailView.as_view()),
    path("user/<uid:pk>/status/", UserStatusView.as_view()),
    # Documents
    path("documents/", DocumentListView.as_view()),
    path("documents/<uid:pk>/", DocumentDetailView.as_view()),
    path("documents/<uid:pk>/download/", DocumentDownloadView.as_view()),
    # Attachments. One generic download for every attachable record type; see
    # the view for why a /media/ URL is not an alternative to it.
    path("attachments/<uid:pk>/download/", AttachmentDownloadView.as_view()),
    # API Settings
    path("api-settings/", DomainList.as_view()),
    path("api-settings/<uid:pk>/", DomainDetailView.as_view()),
    # Activities (for dashboard recent activities)
    path("activities/", ActivityListView.as_view(), name="activities"),
    # Teams (merged from teams app)
    path("teams/", TeamsListView.as_view()),
    path("teams/<uid:pk>/", TeamsDetailView.as_view()),
    # Tags
    path("tags/", TagsListView.as_view()),
    path("tags/<uid:pk>/", TagsDetailView.as_view()),
    path("tags/<uid:pk>/restore/", TagsRestoreView.as_view()),
    path("tags/<uid:pk>/merge/", TagsMergeView.as_view()),
    # Custom fields (per-org schema extension; cross-entity)
    path(
        "custom-fields/",
        CustomFieldDefinitionListCreateView.as_view(),
        name="custom_fields_list_create",
    ),
    path(
        "custom-fields/<uid:pk>/",
        CustomFieldDefinitionDetailView.as_view(),
        name="custom_field_detail",
    ),
    # In-app notifications (per-recipient feed)
    path("notifications/", NotificationListView.as_view(), name="notifications_list"),
    path(
        "notifications/read-all/",
        NotificationReadAllView.as_view(),
        name="notifications_read_all",
    ),
    path(
        "notifications/<uid:pk>/read/",
        NotificationReadView.as_view(),
        name="notifications_read",
    ),
    path(
        "notifications/<uid:pk>/",
        NotificationDetailView.as_view(),
        name="notifications_detail",
    ),
    # Vertical packs: any member may list; apply/clear are ADMIN-only (see
    # common/views/pack_views.py). sample-data/ must precede
    # <str:pack_id>/apply/ so it is never captured as a pack id.
    path("packs/", PackListView.as_view(), name="pack_list"),
    path("packs/sample-data/", PackSampleDataView.as_view(), name="pack_sample_data"),
    path("packs/<str:pack_id>/apply/", PackApplyView.as_view(), name="pack_apply"),
]
