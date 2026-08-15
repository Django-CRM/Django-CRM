"""
Common validators for CRM models.
"""

import datetime
import json
import re
import uuid as uuid_module
from decimal import Decimal
from zoneinfo import available_timezones

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import RegexValidator
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError as DRFValidationError

# E.164 format: +[country code][number] (max 15 digits total)
# Examples: +12025551234, +442071234567
e164_phone_validator = RegexValidator(
    regex=r"^\+[1-9]\d{1,14}$",
    message=_("Phone must be in E.164 format (e.g., +12025551234)"),
)

# Flexible phone format allowing common separators
# Allows: digits, spaces, dashes, parentheses, plus sign
# Examples: +1 (202) 555-1234, 202-555-1234, +44 20 7123 4567
flexible_phone_validator = RegexValidator(
    regex=r"^[\d\s\-\(\)\+\.]{7,25}$",
    message=_(
        "Enter a valid phone number (7-25 characters, digits and separators only)"
    ),
)


def validate_uuid(value, field: str) -> str:
    """Return ``value`` if it is a usable id, otherwise raise a DRF 400.

    Id-valued filters (``tags``, ``assigned_to``, ``account``, ...) arrive as
    raw query-string text and go straight into an ``id`` lookup. Django parses
    them with ``uuid.UUID`` while building the query and raises
    ``django.core.exceptions.ValidationError`` on anything malformed. DRF's
    exception handler does not translate that class, so the request answered
    500 rather than 400: a stale bookmark or a hand-edited URL took the
    endpoint down.

    Parsing here with ``uuid.UUID`` deliberately mirrors what ``UUIDField``
    does, so this accepts exactly what the ORM accepts (hyphenated, bare hex,
    braced and URN forms alike) and cannot drift into rejecting requests that
    used to work.
    """
    try:
        uuid_module.UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        raise DRFValidationError({field: [f"'{value}' is not a valid id."]}) from None
    return value


def validate_uuid_list(value, field: str) -> list:
    """Normalise ``value`` to a list of ids, raising a DRF 400 on any bad one.

    Accepts a list, a single bare string (a body may send one id unwrapped),
    or nothing. Empty entries are dropped rather than rejected: a ``<select>``
    with a blank first option submits ``""``, which means "no filter" and not
    "malformed id".

    The bare-string case also closes a silent bug. Passing a string to an
    ``__in`` lookup makes Django iterate it character by character, so the
    filter matches nothing and reports no error.
    """
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = [value]
    return [validate_uuid(v, field) for v in value if v not in (None, "")]


def payload_id_list(value, field: str) -> list:
    """Ids from a request-body value, whatever shape the client sent.

    The M2M write paths (``tags``, ``contacts``, ``teams``, ``assigned_to``)
    accept three shapes: a real list of ids, a JSON-encoded list because a
    multipart body cannot carry one natively, and a list of
    ``{"id": ..., "name": ...}`` objects because some clients echo back what
    the API gave them. Each view used to unpack that inline, and every copy
    had the same two holes: ``json.loads`` on malformed text raised
    ``JSONDecodeError`` and answered 500, and a malformed id reached the ORM
    and answered 500 as well.

    A bare id string is now accepted as a single-item list. Before, the update
    paths ran it through ``json.loads`` and crashed, while the create paths
    handed it to ``id__in`` unchanged, where Django iterates the string
    character by character and silently matches nothing.
    """
    if value in (None, ""):
        return []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                raise DRFValidationError(
                    {field: [f"{field} must be a list of ids."]}
                ) from None
        else:
            value = [text]
    if not isinstance(value, (list, tuple)):
        raise DRFValidationError({field: [f"{field} must be a list of ids."]})
    ids = [item.get("id") if isinstance(item, dict) else item for item in value]
    return validate_uuid_list(ids, field)


def uuid_param(params, field: str):
    """A single id-valued query parameter, or ``None`` when absent or blank."""
    value = params.get(field)
    if value in (None, ""):
        return None
    return validate_uuid(value, field)


def uuid_list_param(params, field: str) -> list:
    """Every id-valued value for a repeatable query parameter.

    Uses ``getlist`` when the mapping has it (``QueryDict``) and falls back to
    ``get`` for a plain dict, which is what ``request.data`` can be.
    """
    if hasattr(params, "getlist"):
        return validate_uuid_list(params.getlist(field), field)
    return validate_uuid_list(params.get(field), field)


def validate_date(value, field: str) -> datetime.date:
    """The calendar day a date-valued query parameter names.

    The same defect ``validate_uuid`` closes, wearing a different type. Range
    filters (``due_date__gte``, ``created_at__lte``, ``close_date__gte``, ...)
    take raw query-string text and hand it to a date lookup. Django parses it
    while building the query and raises
    ``django.core.exceptions.ValidationError`` on anything malformed, which
    DRF's handler does not translate, so ``?due_date__gte=banana`` answered 500
    rather than 400. ``2026-02-30`` is the same 500 by a different route:
    ``parse_date`` raises ``ValueError`` on a day that does not exist rather
    than answering ``None``, so a check looking only for ``None`` walks past it.

    A full timestamp is accepted as well as a plain date, because callers send
    both: ``mobile/lib/providers/tickets_provider.dart`` sends
    ``toUtc().toIso8601String()``. Neither column type will take that text.
    A DateField parses only ``YYYY-MM-DD``, and the datetime columns are
    filtered through ``__date`` so their range covers the end day, which parses
    only ``YYYY-MM-DD`` too. Resolving the day here rather than returning the
    text is what lets one parameter serve both.

    An aware timestamp resolves in the active timezone, the org's, which is the
    timezone ``__date`` resolves the rows against.
    """
    text = str(value).strip()
    try:
        day = parse_date(text)
        if day is None:
            moment = parse_datetime(text)
            if moment is not None:
                day = (
                    timezone.localtime(moment).date()
                    if timezone.is_aware(moment)
                    else moment.date()
                )
    except ValueError:
        # Well formed, no such moment: "2026-02-30", "2026-02-30T10:00:00".
        day = None
    if day is None:
        raise DRFValidationError(
            {field: [f"'{value}' is not a valid date. Use YYYY-MM-DD."]}
        ) from None
    return day


def date_param(params, field: str):
    """A single date-valued query parameter, or ``None`` when absent or blank."""
    value = params.get(field)
    if value in (None, ""):
        return None
    return validate_date(value, field)


def decimal_param(params, field: str):
    """A single number-valued query parameter, or ``None`` when absent or blank.

    ``?amount__gte=banana`` answered 500 for the same reason the date and id
    filters did: the text reaches a ``DecimalField`` lookup and Django raises
    on the way to the query. Returned as text so the lookup is unchanged.
    """
    value = params.get(field)
    if value in (None, ""):
        return None
    try:
        # `Decimal` happily parses "NaN" and "Infinity", which are not numbers
        # any column here can be compared against, so reject them by name.
        if not Decimal(str(value).strip()).is_finite():
            raise ValueError
    except (ArithmeticError, TypeError, ValueError):
        raise DRFValidationError(
            {field: [f"'{value}' is not a valid number."]}
        ) from None
    return value


def choice_list_param(params, field: str, allowed) -> list:
    """Every value for a repeatable choice-valued query parameter.

    ``?status=New&status=In Progress`` is how a caller asks for either of two
    statuses. A single ``?status=New`` still yields a one-item list, so a view
    can move from ``filter(status=...)`` to ``filter(status__in=...)`` without
    breaking a client that only ever sends one.

    Values outside ``allowed`` are dropped rather than rejected: an unknown
    status matches no row anyway, and a stale bookmark should return an empty
    list, not a 400. Dropping them all leaves ``[]``, which every caller reads
    as "no filter", so an entirely bogus value widens rather than narrows. That
    is the same thing ``?status=Nope`` did before this existed.
    """
    values = (
        params.getlist(field) if hasattr(params, "getlist") else [params.get(field)]
    )
    allowed = set(allowed)
    return [v for v in values if v in allowed]


def normalize_phone(phone: str) -> str:
    """
    Normalize a phone number by removing all non-digit characters.
    Returns the last 10 digits for comparison purposes.
    """
    if not phone:
        return ""
    digits_only = re.sub(r"[^\d]", "", phone)
    # Return last 10 digits for normalized comparison
    return digits_only[-10:] if len(digits_only) >= 10 else digits_only


def validate_iana_timezone(value: str) -> None:
    """Reject anything that is not a name in the system's IANA tz database.

    Timezone is stored as an IANA name (``America/New_York``) rather than a UTC
    offset, because an offset cannot survive daylight saving: ``+05:00`` is a
    fact about one instant, while ``America/New_York`` is a rule that knows when
    the clocks move.

    ``business_hours.models`` re-exports this under its original private name so
    that ``business_hours/migrations/0001_initial.py``, which serialised the path
    ``business_hours.models._validate_iana_tz``, still resolves.
    """
    if value not in available_timezones():
        raise DjangoValidationError(f"{value!r} is not a valid IANA timezone.")
