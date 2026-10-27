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

Whoever turns an endpoint on, or changes what it sends or where (url, events,
format, the secret), becomes its creator and is audited. Otherwise an admin
could re-point a colleague's endpoint at their own server and keep receiving
the org's records after being demoted, since only endpoints a demoted admin
created are paused (webhooks/ownership.py). A description edit moves nothing.

Reads are gated too. A URL often embeds its own credential (Zapier and Slack
hook URLs are bearer secrets), and the delivery log holds copies of records a
member may not be allowed to see.

Another org's id answers 404, never 403, so ids cannot be probed.
"""

from django.db import transaction
from django.http import Http404
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.audit_log import audit_log
from common.lookups import get_scoped_or_404
from common.permissions import HasOrgContext, IsOrgAdmin
from webhooks import events
from webhooks.emit import envelope, queue_delivery
from webhooks.models import PENDING, WebhookDelivery, WebhookEndpoint, generate_secret
from webhooks.serializers import (
    WebhookDeliverySerializer,
    WebhookEndpointCreatedSerializer,
    WebhookEndpointSerializer,
)

MAX_ENDPOINTS_PER_ORG = 10

# Changing any of these makes the caller the endpoint's creator. See the
# module docstring.
TAKE_OVER_FIELDS = ("url", "events", "format")


def _differs(field, new, old):
    # Event order means nothing, and a stored list can predate the catalogue's
    # current order, so resending the same events is no change.
    if field == "events":
        return set(new) != set(old)
    return new != old


def _error(message, code=status.HTTP_400_BAD_REQUEST):
    return Response({"error": True, "errors": message}, status=code)


class WebhookBaseView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext, IsOrgAdmin)

    def get_endpoint(self, request, pk):
        return get_scoped_or_404(WebhookEndpoint, pk, request.profile.org)


class WebhookListCreateView(WebhookBaseView):
    @extend_schema(
        tags=["Webhooks"],
        operation_id="webhooks_list",
        responses={
            200: inline_serializer(
                name="WebhookListResponse",
                fields={
                    "endpoints": WebhookEndpointSerializer(many=True),
                    "event_catalogue": inline_serializer(
                        name="WebhookEventGroup",
                        fields={
                            "module": serializers.CharField(),
                            "label": serializers.CharField(),
                            "events": serializers.ListField(
                                child=serializers.CharField()
                            ),
                        },
                        many=True,
                    ),
                    "limit": serializers.IntegerField(),
                },
            )
        },
    )
    def get(self, request):
        endpoints = WebhookEndpoint.objects.filter(
            org=request.profile.org
        ).select_related("created_by")
        return Response(
            {
                "endpoints": WebhookEndpointSerializer(endpoints, many=True).data,
                "event_catalogue": events.catalogue_json(request.profile.org),
                "limit": MAX_ENDPOINTS_PER_ORG,
            }
        )

    @extend_schema(
        tags=["Webhooks"],
        request=WebhookEndpointSerializer,
        responses={201: WebhookEndpointCreatedSerializer},
    )
    def post(self, request):
        org = request.profile.org
        if WebhookEndpoint.objects.filter(org=org).count() >= MAX_ENDPOINTS_PER_ORG:
            return _error(
                f"An organization can have at most {MAX_ENDPOINTS_PER_ORG} webhooks."
            )
        serializer = WebhookEndpointSerializer(data=request.data, context={"org": org})
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
        # Locked from read to save: deleting an object for good drops its
        # events from these rows under the same lock, and the save writes
        # every column, so a stale read would put them back.
        with transaction.atomic():
            endpoint = (
                WebhookEndpoint.objects.select_for_update()
                .filter(pk=pk, org=request.profile.org)
                .first()
            )
            if endpoint is None:
                raise Http404("No such webhook endpoint.")
            was_active, previous_creator_id = endpoint.is_active, endpoint.created_by_id
            serializer = WebhookEndpointSerializer(
                endpoint,
                data=request.data,
                partial=True,
                context={"org": request.profile.org},
            )
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            changed = [
                field
                for field in TAKE_OVER_FIELDS
                if field in data
                and _differs(field, data[field], getattr(endpoint, field))
            ]
            if data.get("is_active") and not was_active:
                changed.append("is_active")
            if changed:
                serializer.save(created_by=request.user)
            else:
                serializer.save()
        # After the commit, as before: `audit_log` swallows its own insert
        # error, which inside the block would roll the change back unseen.
        if changed:
            audit_log.webhook_taken_over(
                request.user, endpoint, previous_creator_id, changed, request=request
            )
        return Response(serializer.data)

    @extend_schema(tags=["Webhooks"], responses={204: None})
    def delete(self, request, pk):
        self.get_endpoint(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WebhookTestView(WebhookBaseView):
    @extend_schema(
        tags=["Webhooks"], request=None, responses={202: WebhookDeliverySerializer}
    )
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
    @extend_schema(
        tags=["Webhooks"],
        request=None,
        responses={
            200: inline_serializer(
                name="WebhookRotateSecretResponse",
                fields={
                    "secret": serializers.CharField(),
                    "secret_hint": serializers.CharField(),
                },
            )
        },
    )
    def post(self, request, pk):
        endpoint = self.get_endpoint(request, pk)
        previous_creator_id = endpoint.created_by_id
        endpoint.secret = generate_secret()
        endpoint.created_by = request.user
        endpoint.save(
            update_fields=["secret", "created_by", "updated_by", "updated_at"]
        )
        audit_log.webhook_taken_over(
            request.user, endpoint, previous_creator_id, ["secret"], request=request
        )
        return Response(
            {"secret": endpoint.secret, "secret_hint": endpoint.secret_hint}
        )


class WebhookDeliveryListView(WebhookBaseView):
    @extend_schema(
        tags=["Webhooks"],
        responses={
            200: inline_serializer(
                name="WebhookDeliveryListResponse",
                fields={
                    "count": serializers.IntegerField(),
                    "next": serializers.CharField(allow_null=True),
                    "previous": serializers.CharField(allow_null=True),
                    "results": WebhookDeliverySerializer(many=True),
                },
            )
        },
    )
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
    @extend_schema(
        tags=["Webhooks"], request=None, responses={202: WebhookDeliverySerializer}
    )
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
