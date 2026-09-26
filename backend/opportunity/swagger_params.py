"""Schema-only parameter lists for drf-spectacular.

The ``org`` header parameter that used to lead every list here is gone; see
``common/swagger_params.py`` for why.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter

organization_params = []


opportunity_list_get_params = [
    OpenApiParameter("name", OpenApiTypes.STR, OpenApiParameter.QUERY),
    OpenApiParameter("account", OpenApiTypes.STR, OpenApiParameter.QUERY),
    OpenApiParameter("pipeline", OpenApiTypes.UUID, OpenApiParameter.QUERY),
    OpenApiParameter("stage", OpenApiTypes.STR, OpenApiParameter.QUERY),
    OpenApiParameter("lead_source", OpenApiTypes.STR, OpenApiParameter.QUERY),
    OpenApiParameter("tags", OpenApiTypes.STR, OpenApiParameter.QUERY),
]

opportunity_detail_get_params = [
    OpenApiParameter(
        "opportunity_attachment",
        OpenApiParameter.QUERY,
        OpenApiTypes.BINARY,
    ),
    OpenApiParameter("comment", OpenApiTypes.STR, OpenApiParameter.QUERY),
]

opportunity_comment_edit_params = [
    OpenApiParameter("comment", OpenApiTypes.STR, OpenApiParameter.QUERY),
]
