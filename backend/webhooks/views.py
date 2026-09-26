"""Outbound webhook management. Admin-only for every method, reads included.

Routes (all under /api/webhooks/):
    GET    /                                list endpoints, plus the event catalogue
    POST   /                                create; the response carries the secret once
    GET    /<id>/                           one endpoint
    PATCH  /<id>/                           change url, description, events, format, is_active
    DELETE /<id>/                           delete it and its delivery log
    POST   /<id>/test/                      queue a `ping` delivery
    POST   /<id>/rotate-secret/             replace the secret; the response carries it once
    GET    /<id>/deliveries/                paginated delivery log
    POST   /deliveries/<id>/redeliver/      send a settled delivery again, as a new row

Reads are gated too. A URL often embeds its own credential (Zapier and Slack
hook URLs are bearer secrets), and the delivery log holds copies of records a
member may not be allowed to see.

Another org's id answers 404, never 403, so ids cannot be probed.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.lookups import get_scoped_or_404
from common.permissions import HasOrgContext, is_org_admin
from webhooks import events
from webhooks.emit import envelope, queue_delivery
from webhooks.models import PENDING, WebhookDelivery, WebhookEndpoint, generate_secret
from webhooks.serializers import WebhookDeliverySerializer, WebhookEndpointSerializer

MAX_ENDPOINTS_PER_ORG = 10


class IsOrgAdminOrSuperuser(permissions.BasePermission):
    message = "Only an admin can manage webhooks."

    def has_permission(self, request, view):
        profile = getattr(request, "profile", None)
        return is_org_admin(profile) or bool(
            profile is not None and profile.user.is_superuser
        )


def _error(message, code=status.HTTP_400_BAD_REQUEST):
    return Response({"error": True, "errors": message}, status=code)


class WebhookBaseView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext, IsOrgAdminOrSuperuser)

    def get_endpoint(self, request, pk):
        return get_scoped_or_404(WebhookEndpoint, pk, request.profile.org)


class WebhookListCreateView(WebhookBaseView):
    @extend_schema(
        tags=["Webhooks"], operation_id="webhooks_list", responses=OpenApiTypes.OBJECT
    )
    def get(self, request):
        endpoints = WebhookEndpoint.objects.filter(org=request.profile.org)
        return Response(
            {
                "endpoints": WebhookEndpointSerializer(endpoints, many=True).data,
                "event_catalogue": events.catalogue_json(),
                "limit": MAX_ENDPOINTS_PER_ORG,
            }
        )

    @extend_schema(
        tags=["Webhooks"],
        request=WebhookEndpointSerializer,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request):
        org = request.profile.org
        if WebhookEndpoint.objects.filter(org=org).count() >= MAX_ENDPOINTS_PER_ORG:
            return _error(
                f"An organization can have at most {MAX_ENDPOINTS_PER_ORG} webhooks."
            )
        serializer = WebhookEndpointSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        endpoint = serializer.save(org=org)
        # The one response that carries the full secret, besides a rotate.
        return Response(
            {**WebhookEndpointSerializer(endpoint).data, "secret": endpoint.secret},
            status=status.HTTP_201_CREATED,
        )


class WebhookDetailView(WebhookBaseView):
    @extend_schema(tags=["Webhooks"], responses=WebhookEndpointSerializer)
    def get(self, request, pk):
        return Response(WebhookEndpointSerializer(self.get_endpoint(request, pk)).data)

    @extend_schema(
        tags=["Webhooks"],
        request=WebhookEndpointSerializer,
        responses=WebhookEndpointSerializer,
    )
    def patch(self, request, pk):
        endpoint = self.get_endpoint(request, pk)
        serializer = WebhookEndpointSerializer(
            endpoint, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @extend_schema(tags=["Webhooks"], responses={204: None})
    def delete(self, request, pk):
        self.get_endpoint(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WebhookTestView(WebhookBaseView):
    @extend_schema(tags=["Webhooks"], request=None, responses=WebhookDeliverySerializer)
    def post(self, request, pk):
        endpoint = self.get_endpoint(request, pk)
        if not endpoint.is_active:
            return _error("Turn the webhook on before sending a test.")
        payload = envelope(
            endpoint.org_id, events.PING, {"endpoint_id": str(endpoint.id)}
        )
        delivery = queue_delivery(endpoint, events.PING, payload)
        return Response(
            WebhookDeliverySerializer(delivery).data, status=status.HTTP_202_ACCEPTED
        )


class WebhookRotateSecretView(WebhookBaseView):
    @extend_schema(tags=["Webhooks"], request=None, responses=OpenApiTypes.OBJECT)
    def post(self, request, pk):
        endpoint = self.get_endpoint(request, pk)
        endpoint.secret = generate_secret()
        endpoint.save(update_fields=["secret", "updated_at"])
        return Response(
            {"secret": endpoint.secret, "secret_hint": endpoint.secret_hint}
        )


class WebhookDeliveryListView(WebhookBaseView):
    @extend_schema(tags=["Webhooks"], responses=WebhookDeliverySerializer(many=True))
    def get(self, request, pk):
        endpoint = self.get_endpoint(request, pk)
        queryset = WebhookDelivery.objects.filter(
            org=request.profile.org, endpoint=endpoint
        ).order_by("-created_at")
        paginator = LimitOffsetPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            WebhookDeliverySerializer(page, many=True).data
        )


class WebhookRedeliverView(WebhookBaseView):
    @extend_schema(tags=["Webhooks"], request=None, responses=WebhookDeliverySerializer)
    def post(self, request, pk):
        original = get_scoped_or_404(WebhookDelivery, pk, request.profile.org)
        if original.status == PENDING:
            return _error("This delivery is still being attempted.")
        endpoint = original.endpoint
        if not endpoint.is_active:
            return _error("Turn the webhook on before redelivering.")
        delivery = queue_delivery(endpoint, original.event, original.payload)
        return Response(
            WebhookDeliverySerializer(delivery).data, status=status.HTTP_202_ACCEPTED
        )
