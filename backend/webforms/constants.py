"""The fields a web form may collect, and their limits.

A form targets either a Lead or a ticket (`cases.Case`), and each target has
its own whitelist below.

This is a whitelist rather than "any field on Lead" on purpose. A form is
filled in by an anonymous stranger, so the reachable columns have to be a
decision someone made, not whatever the model happens to expose. Assignment,
pipeline stage, probability and deal value are all deliberately absent.

`title` and `salutation` are easy to confuse and mean different things:
`Lead.title` is the lead's subject line ("Website Inquiry"), `Lead.salutation`
is the honorific ("Ms", "Dr"). The legacy endpoint's `title` request parameter
maps to `salutation`, not to `Lead.title`
(see `leads/views/lead_interactions.py`).
"""

from common.utils import COUNTRIES, INDCHOICES

# (value, human label). Value is the attribute name on Lead.
LEAD_FIELD_CHOICES = [
    ("salutation", "Salutation"),
    ("first_name", "First name"),
    ("last_name", "Last name"),
    ("email", "Email"),
    ("phone", "Phone"),
    ("company_name", "Company name"),
    ("job_title", "Job title"),
    ("website", "Website"),
    ("title", "Subject"),
    ("description", "Message"),
    ("city", "City"),
    ("state", "State"),
    ("country", "Country"),
    ("postcode", "Postal code"),
    ("industry", "Industry"),
]

LEAD_FIELD_VALUES = [value for value, _ in LEAD_FIELD_CHOICES]

# Mirrors the max_length on each Lead column so the dynamic serializer can
# reject over-length input with a clean 400 rather than letting the database
# raise. `description` is a TextField with no limit of its own; 5000 is our
# cap, so a single submission cannot be used to write an unbounded blob.
LEAD_FIELD_MAX_LENGTHS = {
    "salutation": 64,
    "first_name": 255,
    "last_name": 255,
    "email": 254,
    "phone": 25,
    "company_name": 255,
    "job_title": 255,
    "website": 255,
    "title": 255,
    "description": 5000,
    "city": 255,
    "state": 255,
    "country": 3,
    "postcode": 64,
    "industry": 255,
}

# Fields whose value must be a member of a choice tuple on Lead. The dynamic
# serializer validates membership rather than trusting the visitor, because
# Django does not enforce `choices` at the database level.
LEAD_FIELD_CHOICE_SOURCES = {
    "country": COUNTRIES,
    "industry": INDCHOICES,
}

# The one field a form has to collect before it can be published. It is the
# key the submission service dedupes on, and without it a repeat submission
# from the same address hits Lead's `UniqueConstraint(Lower("email"), "org")`.
REQUIRED_LEAD_FIELD = "email"

# ---- ticket forms ----------------------------------------------------------
#
# A ticket form writes one Case and finds or creates one Contact. Each value
# lands on exactly one of the two:
#
# * `name` and `description` go on the Case, as its subject and body;
# * `email`, `first_name`, `last_name`, `phone` and `company_name` describe the
#   person, and only ever reach a Contact this submission CREATES. An existing
#   contact matched by email is never edited, because anyone who knows an
#   address can post the form.
#
# Priority, type, status and assignment are deliberately absent. Those are the
# form's own settings (`WebForm.ticket_priority`, `ticket_type`, `assign_to`)
# or routing's decision, never a stranger's: a visitor who could pick
# "Urgent" would.
TICKET_FIELD_CHOICES = [
    ("email", "Email"),
    ("first_name", "First name"),
    ("last_name", "Last name"),
    ("phone", "Phone"),
    ("company_name", "Company name"),
    ("name", "Subject"),
    ("description", "Message"),
]

# `name` accepts more than Case.name's 64 characters on purpose: the service
# truncates it, because refusing a visitor's long subject line would lose the
# ticket over a cosmetic limit. The rest mirror the Contact columns they land
# in (`company_name` is Contact.organization), and `description` shares the
# lead form's 5000 cap.
TICKET_FIELD_MAX_LENGTHS = {
    "email": 254,
    "first_name": 255,
    "last_name": 255,
    "phone": 25,
    "company_name": 255,
    "name": 255,
    "description": 5000,
}

# Case.name is a CharField(max_length=64).
TICKET_SUBJECT_MAX_LENGTH = 64

# The key a ticket form resolves its contact by, and so the field it has to
# collect before it can be published.
REQUIRED_TICKET_FIELD = "email"
