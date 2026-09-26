from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import HasOrgContext, is_org_admin
from invoices.models import Product
from invoices.serializer import ProductSerializer
from opportunity.models import Opportunity, OpportunityLineItem
from opportunity.serializer import (
    OpportunityLineItemCreateSerializer,
    OpportunityLineItemSerializer,
)


class OpportunityLineItemListView(APIView):
    """
    List and create line items for an opportunity.
    GET: List all line items for an opportunity
    POST: Create a new line item for an opportunity
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_opportunity(self, pk):
        """Get opportunity and verify access"""
        return Opportunity.objects.filter(id=pk, org=self.request.profile.org).first()

    def check_opportunity_access(self, opportunity):
        """Check if user has access to the opportunity"""
        if not opportunity:
            return Response(
                {"error": True, "message": "Opportunity not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if (
            not is_org_admin(self.request.profile)
            and not self.request.user.is_superuser
        ):
            if not (
                (self.request.profile.user == opportunity.created_by)
                or (self.request.profile in opportunity.assigned_to.all())
            ):
                return Response(
                    {
                        "error": True,
                        "message": "You do not have permission to access this opportunity",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
        return None

    @extend_schema(
        operation_id="opportunity_line_items_list",
        tags=["Opportunity Line Items"],
        responses={
            200: inline_serializer(
                name="OpportunityLineItemListResponse",
                fields={
                    "line_items": OpportunityLineItemSerializer(many=True),
                    "products": ProductSerializer(many=True),
                    "opportunity_amount": serializers.DecimalField(
                        max_digits=12, decimal_places=2
                    ),
                    "opportunity_amount_source": serializers.CharField(),
                },
            )
        },
    )
    def get(self, request, opportunity_id, *args, **kwargs):
        """List all line items for an opportunity"""
        opportunity = self.get_opportunity(opportunity_id)
        error_response = self.check_opportunity_access(opportunity)
        if error_response:
            return error_response

        line_items = OpportunityLineItem.objects.filter(
            opportunity=opportunity, org=request.profile.org
        ).order_by("order", "created_at")

        # Get available products for the dropdown
        products = Product.objects.filter(
            org=request.profile.org, is_active=True
        ).order_by("name")

        return Response(
            {
                "line_items": OpportunityLineItemSerializer(line_items, many=True).data,
                "products": ProductSerializer(products, many=True).data,
                "opportunity_amount": opportunity.amount,
                "opportunity_amount_source": opportunity.amount_source,
            }
        )

    @extend_schema(
        operation_id="opportunity_line_items_create",
        tags=["Opportunity Line Items"],
        request=OpportunityLineItemCreateSerializer,
        responses={
            201: inline_serializer(
                name="OpportunityLineItemCreateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "line_item": OpportunityLineItemSerializer(),
                    "opportunity_amount": serializers.DecimalField(
                        max_digits=12, decimal_places=2
                    ),
                },
            )
        },
    )
    def post(self, request, opportunity_id, *args, **kwargs):
        """Create a new line item for an opportunity"""
        opportunity = self.get_opportunity(opportunity_id)
        error_response = self.check_opportunity_access(opportunity)
        if error_response:
            return error_response

        serializer = OpportunityLineItemCreateSerializer(
            data=request.data, context={"opportunity": opportunity}
        )
        if serializer.is_valid():
            line_item = serializer.save(
                opportunity=opportunity,
                org=request.profile.org,
            )
            # Refresh opportunity to get updated amount
            opportunity.refresh_from_db()
            return Response(
                {
                    "error": False,
                    "message": "Line item created successfully",
                    "line_item": OpportunityLineItemSerializer(line_item).data,
                    "opportunity_amount": opportunity.amount,
                },
                status=status.HTTP_201_CREATED,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class OpportunityLineItemDetailView(APIView):
    """
    Retrieve, update, or delete a line item.
    GET: Retrieve a single line item
    PUT: Update a line item
    DELETE: Delete a line item
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_opportunity(self, pk):
        """Get opportunity and verify access"""
        return Opportunity.objects.filter(id=pk, org=self.request.profile.org).first()

    def get_line_item(self, opportunity, line_item_id):
        """Get line item"""
        return OpportunityLineItem.objects.filter(
            id=line_item_id,
            opportunity=opportunity,
            org=self.request.profile.org,
        ).first()

    def check_opportunity_access(self, opportunity):
        """Check if user has access to the opportunity"""
        if not opportunity:
            return Response(
                {"error": True, "message": "Opportunity not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if (
            not is_org_admin(self.request.profile)
            and not self.request.user.is_superuser
        ):
            if not (
                (self.request.profile.user == opportunity.created_by)
                or (self.request.profile in opportunity.assigned_to.all())
            ):
                return Response(
                    {
                        "error": True,
                        "message": "You do not have permission to access this opportunity",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
        return None

    @extend_schema(
        operation_id="opportunity_line_items_retrieve",
        tags=["Opportunity Line Items"],
        responses={200: OpportunityLineItemSerializer()},
    )
    def get(self, request, opportunity_id, line_item_id, *args, **kwargs):
        """Retrieve a single line item"""
        opportunity = self.get_opportunity(opportunity_id)
        error_response = self.check_opportunity_access(opportunity)
        if error_response:
            return error_response

        line_item = self.get_line_item(opportunity, line_item_id)
        if not line_item:
            return Response(
                {"error": True, "message": "Line item not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(OpportunityLineItemSerializer(line_item).data)

    @extend_schema(
        operation_id="opportunity_line_items_update",
        tags=["Opportunity Line Items"],
        request=OpportunityLineItemCreateSerializer,
        responses={
            200: inline_serializer(
                name="OpportunityLineItemUpdateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "line_item": OpportunityLineItemSerializer(),
                    "opportunity_amount": serializers.DecimalField(
                        max_digits=12, decimal_places=2
                    ),
                },
            )
        },
    )
    def put(self, request, opportunity_id, line_item_id, *args, **kwargs):
        """Update a line item"""
        opportunity = self.get_opportunity(opportunity_id)
        error_response = self.check_opportunity_access(opportunity)
        if error_response:
            return error_response

        line_item = self.get_line_item(opportunity, line_item_id)
        if not line_item:
            return Response(
                {"error": True, "message": "Line item not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = OpportunityLineItemCreateSerializer(
            line_item,
            data=request.data,
            partial=True,
            context={"opportunity": opportunity},
        )
        if serializer.is_valid():
            line_item = serializer.save()
            # Refresh opportunity to get updated amount
            opportunity.refresh_from_db()
            return Response(
                {
                    "error": False,
                    "message": "Line item updated successfully",
                    "line_item": OpportunityLineItemSerializer(line_item).data,
                    "opportunity_amount": opportunity.amount,
                },
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(
        operation_id="opportunity_line_items_destroy",
        tags=["Opportunity Line Items"],
        responses={
            200: inline_serializer(
                name="OpportunityLineItemDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "opportunity_amount": serializers.DecimalField(
                        max_digits=12, decimal_places=2
                    ),
                },
            )
        },
    )
    def delete(self, request, opportunity_id, line_item_id, *args, **kwargs):
        """Delete a line item"""
        opportunity = self.get_opportunity(opportunity_id)
        error_response = self.check_opportunity_access(opportunity)
        if error_response:
            return error_response

        line_item = self.get_line_item(opportunity, line_item_id)
        if not line_item:
            return Response(
                {"error": True, "message": "Line item not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        line_item.delete()
        # Refresh opportunity to get updated amount
        opportunity.refresh_from_db()
        return Response(
            {
                "error": False,
                "message": "Line item deleted successfully",
                "opportunity_amount": opportunity.amount,
            },
            status=status.HTTP_200_OK,
        )
