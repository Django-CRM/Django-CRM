"""REST endpoints for the business-hours settings page.

Routes (all under /api/business-hours/):
    GET  /calendar/: fetch (and create-on-demand) the
                                            org's default calendar with its
                                            holidays.
    PUT  /calendar/, /calendar/<pk>/: admin update of weekday hours,
                                            timezone, name. Holidays are
                                            managed via the nested endpoints
                                            below to keep payloads small.
    POST /calendar/<pk>/holidays/: add a single holiday.
    DELETE /calendar/<pk>/holidays/<hid>/, remove a single holiday.
"""

from datetime import time

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from business_hours.models import BusinessCalendar, BusinessHoliday
from business_hours.serializers import (
    BusinessCalendarSerializer,
    BusinessHolidaySerializer,
)
from common.permissions import HasOrgContext, may_configure


def _get_or_create_default(org):
    """Return the org's default calendar, materializing one if missing.

    Older orgs created before this migration ran will hit this path the
    first time an admin opens the settings page; the data migration covers
    every org present at deploy time.
    """
    cal = (
        BusinessCalendar.objects.filter(org=org, is_default=True)
        .prefetch_related("holidays")
        .first()
    )
    if cal is not None:
        return cal
    nine = time(9, 0)
    five = time(17, 0)
    return BusinessCalendar.objects.create(
        org=org,
        name="Default",
        timezone="UTC",
        is_default=True,
        monday_open=nine,
        monday_close=five,
        tuesday_open=nine,
        tuesday_close=five,
        wednesday_open=nine,
        wednesday_close=five,
        thursday_open=nine,
        thursday_close=five,
        friday_open=nine,
        friday_close=five,
    )


class BusinessCalendarView(APIView):
    """GET (everyone) / PUT (admin) the org's default BusinessCalendar."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Business hours"], responses={200: BusinessCalendarSerializer})
    def get(self, request, pk=None, *args, **kwargs):
        if pk is None:
            cal = _get_or_create_default(request.profile.org)
        else:
            cal = get_object_or_404(BusinessCalendar, pk=pk, org=request.profile.org)
        return Response(BusinessCalendarSerializer(cal).data)

    @extend_schema(
        tags=["Business hours"],
        request=BusinessCalendarSerializer(partial=True),
        responses={200: BusinessCalendarSerializer},
    )
    def put(self, request, pk=None, *args, **kwargs):
        if not may_configure(request.profile):
            return Response(
                {"error": "Only admins can update business hours."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if pk is None:
            cal = _get_or_create_default(request.profile.org)
        else:
            cal = get_object_or_404(BusinessCalendar, pk=pk, org=request.profile.org)
        serializer = BusinessCalendarSerializer(cal, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        # Re-fetch to include the prefetched holidays in the response.
        cal.refresh_from_db()
        return Response(BusinessCalendarSerializer(cal).data)


class BusinessHolidayListView(APIView):
    """POST a holiday for the calendar (admin-only)."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Business hours"],
        request=BusinessHolidaySerializer,
        # 200 when the calendar already has a holiday on that date.
        responses={201: BusinessHolidaySerializer, 200: BusinessHolidaySerializer},
    )
    def post(self, request, pk, *args, **kwargs):
        if not may_configure(request.profile):
            return Response(
                {"error": "Only admins can update business hours."},
                status=status.HTTP_403_FORBIDDEN,
            )
        cal = get_object_or_404(BusinessCalendar, pk=pk, org=request.profile.org)
        serializer = BusinessHolidaySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Idempotent on (calendar, date): swallow duplicate.
        existing = BusinessHoliday.objects.filter(
            calendar=cal, date=serializer.validated_data["date"]
        ).first()
        if existing is not None:
            return Response(
                BusinessHolidaySerializer(existing).data,
                status=status.HTTP_200_OK,
            )
        holiday = BusinessHoliday.objects.create(
            calendar=cal,
            org=cal.org,
            **serializer.validated_data,
        )
        return Response(
            BusinessHolidaySerializer(holiday).data,
            status=status.HTTP_201_CREATED,
        )


class BusinessHolidayDetailView(APIView):
    """DELETE a single holiday (admin-only)."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Business hours"], responses={204: None})
    def delete(self, request, pk, hid, *args, **kwargs):
        if not may_configure(request.profile):
            return Response(
                {"error": "Only admins can update business hours."},
                status=status.HTTP_403_FORBIDDEN,
            )
        holiday = get_object_or_404(
            BusinessHoliday,
            pk=hid,
            calendar_id=pk,
            org=request.profile.org,
        )
        holiday.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
