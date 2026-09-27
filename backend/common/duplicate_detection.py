"""Possible duplicates of a lead, contact or account, and merging one into another.

Two halves, shared by the three modules:

* **Detection** (`find_duplicates`). Every search starts from the queryset the
  caller may read (`visible_leads_qs`, `visible_contacts_qs`,
  `visible_accounts_qs`), so a record the caller cannot open is never returned
  and never counted. Each rule is one indexed-or-scanned SQL condition in the
  caller's org, and the result is capped at `MAX_RESULTS` rows, so a call made
  while somebody types costs one bounded query rather than loading every row
  with a phone number into Python.
* **Merging** (`merge_records`). Everything that points at the record merged
  away (the loser) is moved onto the record kept (the keeper), the keeper's
  blank fields take the loser's values, and the loser is deleted. The caller
  holds both rows locked inside one transaction.

Matching rules, and why they are this narrow:

* Email: case-insensitive equality.
* Phone: the last ten digits (or every digit of a shorter number) equal,
  whatever the separators. Matched by a regular expression in the database, so
  "+1 (202) 555-0147" finds "202.555.0147" without reading any row into Python.
  Fewer than seven digits is not a phone number worth matching on.
* Person name (leads, contacts): first AND last name, case-insensitive. One
  half alone matches every "John".
* Lead company: exact, and only when the lead has no person name. Two people at
  one company are two leads, not a duplicate; the old ``icontains`` rule also
  paired "Tech" with every company containing it.
* Account name: equal once case, punctuation, a leading "The" and a trailing
  legal suffix ("Inc", "Ltd", "LLC", ...) are set aside, so "Acme" finds
  "ACME, Inc." The old rule matched the first word with ``istartswith``, which
  paired every "Global ..." or "The ..." account in the org with every other.
* Website: the same host, ignoring scheme, "www." and path.
"""

import re

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db.models import BooleanField, Q

from common.validators import normalize_phone

MAX_RESULTS = 10

# The shortest normalised phone number worth matching on.
MIN_PHONE_DIGITS = 7

LEGAL_SUFFIXES = (
    "inc",
    "incorporated",
    "llc",
    "llp",
    "ltd",
    "limited",
    "corp",
    "corporation",
    "co",
    "company",
    "gmbh",
    "plc",
    "pvt",
    "pty",
    "ag",
    "bv",
    "sa",
)


def normalize_domain(website):
    """The host of a website, lower case, without scheme, "www." or path."""
    if not website:
        return ""
    domain = website.strip().lower()
    domain = re.sub(r"^[a-z]+://", "", domain)
    domain = domain.split("/")[0].split("?")[0].split("#")[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def account_name_core(name):
    """An account name with case, punctuation, a leading "The" and trailing
    legal suffixes set aside, as a list of words. "The Acme Co., Ltd." and
    "acme" both give ``["acme"]``."""
    words = re.sub(r"[.,]", " ", (name or "").lower()).split()
    if words[:1] == ["the"]:
        words = words[1:]
    while words and words[-1] in LEGAL_SUFFIXES:
        words = words[:-1]
    return words


def _phone_regex(phone):
    """A pattern matching any stored phone whose normalised form equals this
    one's, or None when there are too few digits to match on."""
    digits = normalize_phone(phone)
    if len(digits) < MIN_PHONE_DIGITS:
        return None
    body = "[^0-9]*".join(digits) + "[^0-9]*$"
    # Ten digits are the tail of a number that may carry a country code, so
    # anything may come before them. A shorter number must be the whole number.
    return body if len(digits) == 10 else "^[^0-9]*" + body


def _domain_regex(website):
    domain = normalize_domain(website)
    if "." not in domain or len(domain) < 4:
        return None
    return r"^([a-z]+://)?(www\.)?" + re.escape(domain) + r"([/?#].*)?$"


def _account_name_regex(name):
    words = account_name_core(name)
    if not words or len("".join(words)) < 3:
        return None
    suffixes = "|".join(LEGAL_SUFFIXES)
    return (
        r"^\s*(the\s+)?"
        + r"[\s.,]+".join(re.escape(word) for word in words)
        + rf"([\s.,]+({suffixes}))*[\s.,]*$"
    )


def _clean(value):
    return (value or "").strip()


def duplicate_query(model_name, criteria):
    """The OR of every rule `criteria` supports, or None when it supports none.

    ``criteria`` holds the fields a create form has typed so far, or a saved
    record's own values: ``email``, ``phone``, ``first_name``, ``last_name``,
    ``company_name`` (leads), ``name`` and ``website`` (accounts).
    """
    q = Q()
    email = _clean(criteria.get("email"))
    if email:
        q |= Q(email__iexact=email)
    phone = _phone_regex(criteria.get("phone"))
    if phone:
        q |= Q(phone__regex=phone)

    if model_name in ("lead", "contact"):
        first = _clean(criteria.get("first_name"))
        last = _clean(criteria.get("last_name"))
        if first and last:
            q |= Q(first_name__iexact=first, last_name__iexact=last)
        company = _clean(criteria.get("company_name"))
        if model_name == "lead" and company and not (first or last):
            q |= Q(company_name__iexact=company)
    elif model_name == "account":
        name = _clean(criteria.get("name"))
        pattern = _account_name_regex(name)
        if pattern:
            q |= Q(name__iregex=pattern)
        elif name:
            q |= Q(name__iexact=name)
        website = _domain_regex(criteria.get("website"))
        if website:
            q |= Q(website__iregex=website)
    return q or None


def matched_on(model_name, criteria, record):
    """Which rules paired ``record`` with ``criteria``, for the reader."""
    reasons = []
    email = _clean(criteria.get("email")).lower()
    if email and (record.email or "").lower() == email:
        reasons.append("email")
    phone = normalize_phone(criteria.get("phone"))
    if len(phone) >= MIN_PHONE_DIGITS and normalize_phone(record.phone) == phone:
        reasons.append("phone")
    if model_name in ("lead", "contact"):
        first = _clean(criteria.get("first_name")).lower()
        last = _clean(criteria.get("last_name")).lower()
        if (
            first
            and last
            and (record.first_name or "").lower() == first
            and (record.last_name or "").lower() == last
        ):
            reasons.append("name")
        company = _clean(criteria.get("company_name")).lower()
        if (
            model_name == "lead"
            and company
            and (record.company_name or "").lower() == company
        ):
            reasons.append("company")
    elif model_name == "account":
        name = criteria.get("name")
        if name and account_name_core(record.name) == account_name_core(name):
            reasons.append("name")
        domain = normalize_domain(criteria.get("website"))
        if domain and normalize_domain(record.website) == domain:
            reasons.append("website")
    return reasons


def find_duplicates(visible, criteria, exclude_id=None):
    """Up to `MAX_RESULTS` active records in ``visible`` that look like
    ``criteria``, newest first, each with the rules it matched on.

    ``visible`` is the caller's read-rule queryset, already scoped to their
    org, and is the only source of rows: a record the caller may not open can
    neither appear nor be counted.
    """
    model_name = visible.model._meta.model_name
    q = duplicate_query(model_name, criteria)
    if q is None:
        return []
    qs = visible.filter(q, is_active=True)
    if model_name == "lead":
        # A converted lead is out of every list and cannot be merged.
        qs = qs.exclude(status="converted")
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    rows = qs.order_by("-created_at")[:MAX_RESULTS]
    return [(record, matched_on(model_name, criteria, record)) for record in rows]


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------

# Relations that are NOT moved to the keeper and die with the loser instead.
# A portal sign-in code was minted for the loser's address; moving it would let
# whoever holds that address sign in to the keeper's portal with it.
LEFT_WITH_LOSER = {("common.PortalLoginToken", "contact")}

# Fields the merge never copies, whatever the keeper holds. Identity, tenancy
# and audit columns are server facts, and a lead's workflow position (status,
# pipeline stage, card order) is the keeper's to keep. Custom fields are
# filled key by key below.
NOT_FILLED = {
    "id",
    "org",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
    "status",
    "stage",
    "kanban_order",
    "custom_fields",
}

# Who owns the record. Treated like a scalar field: the keeper's owners stay,
# and the loser's are taken only when the keeper has none. Every other
# many-to-many (tags, linked contacts) is a union, because dropping a link is
# losing data.
OWNERSHIP_M2M = {"assigned_to", "teams"}


def _generic_keyed_models():
    """Every model that points at a record by ``(content_type, object_id)``:
    comments and attachments here, and any installed app's own (the enterprise
    Salesforce import keeps its record map this way). The Django admin log is
    left alone: it is the admin's history, and its ``object_id`` is text."""
    for candidate in apps.get_models():
        fields = {f.name: f for f in candidate._meta.concrete_fields}
        content_type = fields.get("content_type")
        if (
            content_type is not None
            and content_type.is_relation
            and content_type.related_model is ContentType
            and "object_id" in fields
            and candidate._meta.label != "admin.LogEntry"
        ):
            yield candidate


# Fields that only mean something together. A group is filled from the loser
# only when the keeper holds none of it, and then as a whole: a city from one
# record under a street and country from the other describes no real place,
# and a first name from one person beside a last name from the other names
# nobody.
FIELD_GROUPS = (
    ("address_line", "city", "state", "postcode", "country"),
    ("salutation", "first_name", "last_name"),
)

# An amount is a number in a currency. The loser's amount is taken only when
# the keeper has none and its currency is blank or the same, and then with
# its currency. A currency is never filled on its own: under the keeper's
# amount it would relabel that amount.
MONEY_FIELDS = (("opportunity_amount", "currency"), ("annual_revenue", "currency"))


def _fill_grouped(keeper, loser, names):
    """Fill `FIELD_GROUPS` and `MONEY_FIELDS` from the loser, each as a unit,
    and return every field name they cover so the plain fill leaves them."""
    covered = set()
    for group in FIELD_GROUPS:
        present = [name for name in group if name in names]
        covered.update(present)
        if present and all(_is_blank(getattr(keeper, n)) for n in present):
            for name in present:
                setattr(keeper, name, getattr(loser, name))
    for amount, currency in MONEY_FIELDS:
        if amount not in names or currency not in names:
            continue
        covered.update((amount, currency))
        theirs = getattr(loser, currency)
        mine = getattr(keeper, currency)
        if (
            _is_blank(getattr(keeper, amount))
            and not _is_blank(getattr(loser, amount))
            and (_is_blank(mine) or mine == theirs)
        ):
            setattr(keeper, amount, getattr(loser, amount))
            setattr(keeper, currency, theirs)
    return covered


def _is_blank(value):
    return value is None or value == "" or value == [] or value == {}


def _move_through_rows(through, mine, other, keeper, loser):
    """Repoint ``through`` rows from the loser to the keeper, skipping any
    pairing the keeper already has. What is left goes with the loser's delete."""
    already = through.objects.filter(**{mine: keeper}).values(other)
    through.objects.filter(**{mine: loser}).exclude(**{f"{other}__in": already}).update(
        **{mine: keeper}
    )


def merge_records(keeper, loser):
    """Merge ``loser`` into ``keeper`` and delete ``loser``.

    The caller has already checked who may do this, that both rows are in one
    org, and holds both rows locked in a transaction. The order matters:
    links move first, the loser is deleted (taking its portal codes and any
    link the keeper already had with it), and only then is the keeper saved
    with its filled blanks, because email (leads, contacts) is unique per org
    and the loser still held it until the delete.

    Every foreign key and many-to-many that points at this model is found from
    ``_meta`` rather than listed by hand, so one added later moves too;
    `common/tests/test_duplicate_merge.py` pins the list so that a new one is
    looked at, not just moved.

    The loser's ``.deleted`` webhook payload is built here, before anything
    moves, because its assignees may move to the keeper below and a snapshot
    taken at the delete would then list none. `webhooks.signals` keeps a
    snapshot that is already set.
    """
    from common.models import Activity
    from webhooks.emit import SPECS

    model = type(keeper)
    _prefix, build = SPECS[model._meta.label_lower]
    loser._webhook_snapshot = build(loser)

    for field in model._meta.many_to_many:
        through = field.remote_field.through
        mine, other = field.m2m_field_name(), field.m2m_reverse_field_name()
        if (
            field.name in OWNERSHIP_M2M
            and through.objects.filter(**{mine: keeper}).exists()
        ):
            continue
        _move_through_rows(through, mine, other, keeper, loser)

    for rel in model._meta.related_objects:
        if (rel.related_model._meta.label, rel.field.name) in LEFT_WITH_LOSER:
            continue
        if rel.many_to_many:
            _move_through_rows(
                rel.through,
                rel.field.m2m_reverse_field_name(),
                rel.field.m2m_field_name(),
                keeper,
                loser,
            )
        else:
            rel.related_model._base_manager.filter(**{rel.field.name: loser}).update(
                **{rel.field.name: keeper}
            )

    content_type = ContentType.objects.get_for_model(model)
    for generic in _generic_keyed_models():
        rows = generic._base_manager.filter(
            content_type=content_type, object_id=loser.pk
        )
        if any(f.name == "org" for f in generic._meta.concrete_fields):
            rows = rows.filter(org_id=keeper.org_id)
        rows.update(object_id=keeper.pk)
    Activity.objects.filter(
        org_id=keeper.org_id, entity_type=model.__name__, entity_id=loser.pk
    ).update(entity_id=keeper.pk)

    names = {field.name for field in model._meta.concrete_fields}
    grouped = _fill_grouped(keeper, loser, names)
    for field in model._meta.concrete_fields:
        if field.name in NOT_FILLED or field.name in grouped:
            continue
        if isinstance(field, BooleanField):
            # Never blank. The one exception is a customer's request not to be
            # called: whichever record carries it, the merged one does.
            if field.name == "do_not_call" and getattr(loser, field.attname):
                setattr(keeper, field.attname, True)
            continue
        if _is_blank(getattr(keeper, field.attname)):
            value = getattr(loser, field.attname)
            if not _is_blank(value):
                setattr(keeper, field.attname, value)

    custom = dict(keeper.custom_fields or {})
    for key, value in (loser.custom_fields or {}).items():
        if _is_blank(custom.get(key)) and not _is_blank(value):
            custom[key] = value
    keeper.custom_fields = custom

    loser.delete()
    keeper.save()
    return keeper
