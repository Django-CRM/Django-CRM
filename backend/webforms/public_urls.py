"""Public web form routes, mounted at /api/public/forms/.

`org_id` is the FIRST segment after the fixed prefix, and that ordering is
load-bearing in exactly the way `common/portal_urls.py` documents.
`RequireOrgContext.EXEMPT_PATHS` is prefix-matched with `startswith`, so the
fixed `/api/public/forms/` prefix exempts these routes and nothing else.
Moving `org_id` ahead of the prefix would make the exemptable portion variable
and there would be no way to exempt these without exempting more.
"""

from django.urls import path

from webforms import public_views

app_name = "public_webforms"

urlpatterns = [
    path(
        "<uuid:org_id>/<uuid:form_id>/embed/",
        public_views.WebFormEmbedView.as_view(),
        name="embed",
    ),
    path(
        "<uuid:org_id>/<uuid:form_id>/embed.js",
        public_views.WebFormEmbedJsView.as_view(),
        name="embed_js",
    ),
    path(
        "<uuid:org_id>/<uuid:form_id>/submit/",
        public_views.WebFormSubmitView.as_view(),
        name="submit",
    ),
]
