"""
Lead conversion service functions
"""

from django.contrib.contenttypes.models import ContentType
from rest_framework.exceptions import ValidationError

from accounts.access import has_account_access
from accounts.models import Account
from common.models import Attachments, Comment
from contacts.access import has_contact_access, visible_contacts_qs
from contacts.models import Contact
from opportunity.models import Opportunity

CONVERSION_REFUSED = (
    "An account named '{name}' already exists and you don't have access to it. "
    "Ask an admin to convert this lead or give you access."
)


def conversion_account_name(lead):
    """The name of the account a conversion files ``lead`` under."""
    if lead.company_name:
        return lead.company_name
    return f"{lead.first_name} {lead.last_name}"


def conversion_refusal(request, lead):
    """Why ``request.profile`` may not convert ``lead``, or None if they may.

    Conversion joins an existing account of the same name and copies the
    lead's assignees onto it, so converting into an account the caller cannot
    open would make it theirs (and every contact linked to it). That is
    refused. An account they can open, or no match at all, is fine.

    ``lead`` only needs ``company_name``, ``first_name`` and ``last_name``, so
    a view can ask with the values a save is about to write, before writing.
    """
    matches = Account.objects.filter(
        name__iexact=conversion_account_name(lead), org=request.profile.org
    ).prefetch_related("assigned_to")
    for account in matches:
        if not has_account_access(request.profile, request.user, account):
            return CONVERSION_REFUSED.format(name=account.name)
    return None


def convert_lead_to_account(lead_obj, request, create_opportunity=True):
    """
    Convert a Lead to Account, Contact, and optionally Opportunity.

    This function:
    1. Creates an Account from lead data
    2. Creates a Contact from lead data (if lead has email), or links the
       existing one with that email if the converter may open it
    3. Optionally creates an Opportunity (if create_opportunity=True and lead has opportunity_amount)
    4. Copies tags, assigned_to, teams
    5. Migrates Comments and Attachments
    6. Auto-links the Lead.contacts the converter may open to the new Account
    7. Sets lead status to "converted"

    Args:
        lead_obj: The Lead instance to convert
        request: The HTTP request (for user/org context)
        create_opportunity: Whether to create an Opportunity (default: True)

    Returns:
        tuple: (account, contact, opportunity) - the created entities (Contact/Opportunity may be None)

    Raises ``ValidationError`` before writing anything when the matching
    account is one the converter cannot open (see `conversion_refusal`). The
    views ask first, before their own save; this is for any caller that does
    not.
    """
    refusal = conversion_refusal(request, lead_obj)
    if refusal:
        raise ValidationError({"status": [refusal]})

    # Create or get existing Account (handles unique_account_name_per_org constraint)
    account_name = conversion_account_name(lead_obj)
    account, created = Account.objects.get_or_create(
        name__iexact=account_name,
        org=request.profile.org,
        defaults={
            "created_by": request.profile.user,
            "name": account_name,
            "email": lead_obj.email,
            "phone": lead_obj.phone,
            "description": lead_obj.description,
            "website": lead_obj.website,
            "is_active": True,
            "address_line": lead_obj.address_line,
            "city": lead_obj.city,
            "state": lead_obj.state,
            "postcode": lead_obj.postcode,
            "country": lead_obj.country,
        },
    )

    # Copy tags
    for tag in lead_obj.tags.all():
        account.tags.add(tag)

    # Copy assigned users
    for profile in lead_obj.assigned_to.all():
        account.assigned_to.add(profile)

    # Copy teams
    for team in lead_obj.teams.all():
        account.teams.add(team)

    # Move comments from Lead to Account
    lead_ct = ContentType.objects.get_for_model(lead_obj.__class__)
    account_ct = ContentType.objects.get_for_model(Account)
    Comment.objects.filter(
        content_type=lead_ct,
        object_id=lead_obj.id,
        org=request.profile.org,
    ).update(content_type=account_ct, object_id=account.id)

    # Move attachments from Lead to Account
    Attachments.objects.filter(
        content_type=lead_ct,
        object_id=lead_obj.id,
        org=request.profile.org,
    ).update(content_type=account_ct, object_id=account.id)

    # Create or get existing Contact from Lead data (handles unique_contact_email_per_org constraint)
    contact = None
    if lead_obj.email:
        contact, contact_created = Contact.objects.get_or_create(
            email__iexact=lead_obj.email,
            org=request.profile.org,
            defaults={
                "first_name": lead_obj.first_name or "",
                "last_name": lead_obj.last_name or "",
                "email": lead_obj.email,
                "phone": lead_obj.phone,
                "organization": lead_obj.company_name or "",
                "title": lead_obj.job_title,
                "description": lead_obj.description,
                "address_line": lead_obj.address_line,
                "city": lead_obj.city,
                "state": lead_obj.state,
                "postcode": lead_obj.postcode,
                "country": lead_obj.country,
                "created_by": request.profile.user,
                "account": account,
            },
        )
        if contact_created:
            contact.assigned_to.set(lead_obj.assigned_to.all())
        elif not has_contact_access(request.profile, contact):
            # The email matched a contact the converter may not open. Linking
            # it to this account, which the lead's assignees now hold, would
            # make it theirs to read, so it is left exactly as it was.
            contact = None
        elif not contact.account:
            # Link existing contact to the account if not already linked
            contact.account = account
            contact.save(update_fields=["account"])

        # Also link contact to account via M2M (for backwards compatibility)
        if contact is not None:
            account.contacts.add(contact)

    # Auto-link the lead's contacts to the new Account, the ones the converter
    # may open. The lead can carry others (a member's edit keeps what it
    # cannot see), and the account would hand those over for the same reason.
    account.contacts.add(
        *visible_contacts_qs(request.profile).filter(
            id__in=lead_obj.contacts.values("id")
        )
    )

    # Create Opportunity if requested and lead has opportunity data
    opportunity = None
    if create_opportunity and (
        lead_obj.opportunity_amount or lead_obj.first_name or lead_obj.company_name
    ):
        # Construct opportunity name from available lead data
        opp_name_parts = []
        if lead_obj.company_name:
            opp_name_parts.append(lead_obj.company_name)
        if lead_obj.first_name or lead_obj.last_name:
            name = " ".join(filter(None, [lead_obj.first_name, lead_obj.last_name]))
            opp_name_parts.append(name)
        opp_name = (
            " - ".join(opp_name_parts)
            if opp_name_parts
            else f"Opportunity {lead_obj.id}"
        )

        opportunity = Opportunity.objects.create(
            name=opp_name,
            account=account,
            amount=lead_obj.opportunity_amount,
            probability=lead_obj.probability or 0,
            closed_on=lead_obj.close_date,
            lead_source=lead_obj.source,
            description=lead_obj.description,
            stage="QUALIFICATION",  # Initial stage after lead conversion
            created_by=request.profile.user,
            org=request.profile.org,
        )
        # Copy assigned users to opportunity
        opportunity.assigned_to.set(lead_obj.assigned_to.all())
        opportunity.teams.set(lead_obj.teams.all())
        opportunity.tags.set(lead_obj.tags.all())
        # Link contact to opportunity
        if contact:
            opportunity.contacts.add(contact)

    # Update lead status to converted
    lead_obj.status = "converted"
    lead_obj.save()

    return account, contact, opportunity
