"""The set of Lead columns a web form may collect, and their limits.

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
