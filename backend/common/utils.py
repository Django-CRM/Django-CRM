import pytz
from django.utils.translation import gettext_lazy as _

INDCHOICES = (
    ("ADVERTISING", "ADVERTISING"),
    ("AGRICULTURE", "AGRICULTURE"),
    ("APPAREL & ACCESSORIES", "APPAREL & ACCESSORIES"),
    ("AUTOMOTIVE", "AUTOMOTIVE"),
    ("BANKING", "BANKING"),
    ("BIOTECHNOLOGY", "BIOTECHNOLOGY"),
    ("BUILDING MATERIALS & EQUIPMENT", "BUILDING MATERIALS & EQUIPMENT"),
    ("CHEMICAL", "CHEMICAL"),
    ("COMPUTER", "COMPUTER"),
    ("EDUCATION", "EDUCATION"),
    ("ELECTRONICS", "ELECTRONICS"),
    ("ENERGY", "ENERGY"),
    ("ENTERTAINMENT & LEISURE", "ENTERTAINMENT & LEISURE"),
    ("FINANCE", "FINANCE"),
    ("FOOD & BEVERAGE", "FOOD & BEVERAGE"),
    ("GROCERY", "GROCERY"),
    ("HEALTHCARE", "HEALTHCARE"),
    ("INSURANCE", "INSURANCE"),
    ("LEGAL", "LEGAL"),
    ("MANUFACTURING", "MANUFACTURING"),
    ("PUBLISHING", "PUBLISHING"),
    ("REAL ESTATE", "REAL ESTATE"),
    ("SERVICE", "SERVICE"),
    ("SOFTWARE", "SOFTWARE"),
    ("SPORTS", "SPORTS"),
    ("TECHNOLOGY", "TECHNOLOGY"),
    ("TELECOMMUNICATIONS", "TELECOMMUNICATIONS"),
    ("TELEVISION", "TELEVISION"),
    ("TRANSPORTATION", "TRANSPORTATION"),
    ("VENTURE CAPITAL", "VENTURE CAPITAL"),
)

TYPECHOICES = (
    ("CUSTOMER", "CUSTOMER"),
    ("INVESTOR", "INVESTOR"),
    ("PARTNER", "PARTNER"),
    ("RESELLER", "RESELLER"),
)

ROLES = (
    ("ADMIN", "ADMIN"),
    ("USER", "USER"),
)

LEAD_STATUS = (
    ("assigned", "Assigned"),
    ("in process", "In Process"),
    ("converted", "Converted"),
    ("recycled", "Recycled"),
    ("closed", "Closed"),
)


LEAD_SOURCE = (
    ("call", "Call"),
    ("email", "Email"),
    ("existing customer", "Existing Customer"),
    ("partner", "Partner"),
    ("public relations", "Public Relations"),
    ("compaign", "Campaign"),
    ("other", "Other"),
)

STATUS_CHOICE = (
    ("New", "New"),
    ("Assigned", "Assigned"),
    ("Pending", "Pending"),
    ("Closed", "Closed"),
    ("Rejected", "Rejected"),
    ("Duplicate", "Duplicate"),
)

PRIORITY_CHOICE = (
    ("Low", "Low"),
    ("Normal", "Normal"),
    ("High", "High"),
    ("Urgent", "Urgent"),
)

CASE_TYPE = (("Question", "Question"), ("Incident", "Incident"), ("Problem", "Problem"))

STAGES = (
    ("PROSPECTING", "Prospecting"),
    ("QUALIFICATION", "Qualification"),
    ("PROPOSAL", "Proposal"),
    ("NEGOTIATION", "Negotiation"),
    ("CLOSED_WON", "Closed Won"),
    ("CLOSED_LOST", "Closed Lost"),
)

OPPORTUNITY_TYPES = (
    ("NEW_BUSINESS", "New Business"),
    ("EXISTING_BUSINESS", "Existing Business"),
    ("RENEWAL", "Renewal"),
    ("UPSELL", "Upsell"),
    ("CROSS_SELL", "Cross-sell"),
)

SOURCES = (
    ("NONE", "NONE"),
    ("CALL", "CALL"),
    ("EMAIL", " EMAIL"),
    ("EXISTING CUSTOMER", "EXISTING CUSTOMER"),
    ("PARTNER", "PARTNER"),
    ("PUBLIC RELATIONS", "PUBLIC RELATIONS"),
    ("CAMPAIGN", "CAMPAIGN"),
    ("WEBSITE", "WEBSITE"),
    ("OTHER", "OTHER"),
)

EVENT_PARENT_TYPE = ((10, "Account"), (13, "Lead"), (14, "Opportunity"), (11, "Case"))

EVENT_STATUS = (
    ("Planned", "Planned"),
    ("Held", "Held"),
    ("Not Held", "Not Held"),
    ("Not Started", "Not Started"),
    ("Started", "Started"),
    ("Completed", "Completed"),
    ("Canceled", "Canceled"),
    ("Deferred", "Deferred"),
)


COUNTRIES = (
    ("GB", _("United Kingdom")),
    ("AF", _("Afghanistan")),
    ("AX", _("Aland Islands")),
    ("AL", _("Albania")),
    ("DZ", _("Algeria")),
    ("AS", _("American Samoa")),
    ("AD", _("Andorra")),
    ("AO", _("Angola")),
    ("AI", _("Anguilla")),
    ("AQ", _("Antarctica")),
    ("AG", _("Antigua and Barbuda")),
    ("AR", _("Argentina")),
    ("AM", _("Armenia")),
    ("AW", _("Aruba")),
    ("AU", _("Australia")),
    ("AT", _("Austria")),
    ("AZ", _("Azerbaijan")),
    ("BS", _("Bahamas")),
    ("BH", _("Bahrain")),
    ("BD", _("Bangladesh")),
    ("BB", _("Barbados")),
    ("BY", _("Belarus")),
    ("BE", _("Belgium")),
    ("BZ", _("Belize")),
    ("BJ", _("Benin")),
    ("BM", _("Bermuda")),
    ("BT", _("Bhutan")),
    ("BO", _("Bolivia")),
    ("BA", _("Bosnia and Herzegovina")),
    ("BW", _("Botswana")),
    ("BV", _("Bouvet Island")),
    ("BR", _("Brazil")),
    ("IO", _("British Indian Ocean Territory")),
    ("BN", _("Brunei Darussalam")),
    ("BG", _("Bulgaria")),
    ("BF", _("Burkina Faso")),
    ("BI", _("Burundi")),
    ("KH", _("Cambodia")),
    ("CM", _("Cameroon")),
    ("CA", _("Canada")),
    ("CV", _("Cape Verde")),
    ("KY", _("Cayman Islands")),
    ("CF", _("Central African Republic")),
    ("TD", _("Chad")),
    ("CL", _("Chile")),
    ("CN", _("China")),
    ("CX", _("Christmas Island")),
    ("CC", _("Cocos (Keeling) Islands")),
    ("CO", _("Colombia")),
    ("KM", _("Comoros")),
    ("CG", _("Congo")),
    ("CD", _("Congo, The Democratic Republic of the")),
    ("CK", _("Cook Islands")),
    ("CR", _("Costa Rica")),
    ("CI", _("Cote d'Ivoire")),
    ("HR", _("Croatia")),
    ("CU", _("Cuba")),
    ("CY", _("Cyprus")),
    ("CZ", _("Czech Republic")),
    ("DK", _("Denmark")),
    ("DJ", _("Djibouti")),
    ("DM", _("Dominica")),
    ("DO", _("Dominican Republic")),
    ("EC", _("Ecuador")),
    ("EG", _("Egypt")),
    ("SV", _("El Salvador")),
    ("GQ", _("Equatorial Guinea")),
    ("ER", _("Eritrea")),
    ("EE", _("Estonia")),
    ("ET", _("Ethiopia")),
    ("FK", _("Falkland Islands (Malvinas)")),
    ("FO", _("Faroe Islands")),
    ("FJ", _("Fiji")),
    ("FI", _("Finland")),
    ("FR", _("France")),
    ("GF", _("French Guiana")),
    ("PF", _("French Polynesia")),
    ("TF", _("French Southern Territories")),
    ("GA", _("Gabon")),
    ("GM", _("Gambia")),
    ("GE", _("Georgia")),
    ("DE", _("Germany")),
    ("GH", _("Ghana")),
    ("GI", _("Gibraltar")),
    ("GR", _("Greece")),
    ("GL", _("Greenland")),
    ("GD", _("Grenada")),
    ("GP", _("Guadeloupe")),
    ("GU", _("Guam")),
    ("GT", _("Guatemala")),
    ("GG", _("Guernsey")),
    ("GN", _("Guinea")),
    ("GW", _("Guinea-Bissau")),
    ("GY", _("Guyana")),
    ("HT", _("Haiti")),
    ("HM", _("Heard Island and McDonald Islands")),
    ("VA", _("Holy See (Vatican City State)")),
    ("HN", _("Honduras")),
    ("HK", _("Hong Kong")),
    ("HU", _("Hungary")),
    ("IS", _("Iceland")),
    ("IN", _("India")),
    ("ID", _("Indonesia")),
    ("IR", _("Iran, Islamic Republic of")),
    ("IQ", _("Iraq")),
    ("IE", _("Ireland")),
    ("IM", _("Isle of Man")),
    ("IL", _("Israel")),
    ("IT", _("Italy")),
    ("JM", _("Jamaica")),
    ("JP", _("Japan")),
    ("JE", _("Jersey")),
    ("JO", _("Jordan")),
    ("KZ", _("Kazakhstan")),
    ("KE", _("Kenya")),
    ("KI", _("Kiribati")),
    ("KP", _("Korea, Democratic People's Republic of")),
    ("KR", _("Korea, Republic of")),
    ("KW", _("Kuwait")),
    ("KG", _("Kyrgyzstan")),
    ("LA", _("Lao People's Democratic Republic")),
    ("LV", _("Latvia")),
    ("LB", _("Lebanon")),
    ("LS", _("Lesotho")),
    ("LR", _("Liberia")),
    ("LY", _("Libyan Arab Jamahiriya")),
    ("LI", _("Liechtenstein")),
    ("LT", _("Lithuania")),
    ("LU", _("Luxembourg")),
    ("MO", _("Macao")),
    ("MK", _("Macedonia, The Former Yugoslav Republic of")),
    ("MG", _("Madagascar")),
    ("MW", _("Malawi")),
    ("MY", _("Malaysia")),
    ("MV", _("Maldives")),
    ("ML", _("Mali")),
    ("MT", _("Malta")),
    ("MH", _("Marshall Islands")),
    ("MQ", _("Martinique")),
    ("MR", _("Mauritania")),
    ("MU", _("Mauritius")),
    ("YT", _("Mayotte")),
    ("MX", _("Mexico")),
    ("FM", _("Micronesia, Federated States of")),
    ("MD", _("Moldova")),
    ("MC", _("Monaco")),
    ("MN", _("Mongolia")),
    ("ME", _("Montenegro")),
    ("MS", _("Montserrat")),
    ("MA", _("Morocco")),
    ("MZ", _("Mozambique")),
    ("MM", _("Myanmar")),
    ("NA", _("Namibia")),
    ("NR", _("Nauru")),
    ("NP", _("Nepal")),
    ("NL", _("Netherlands")),
    ("AN", _("Netherlands Antilles")),
    ("NC", _("New Caledonia")),
    ("NZ", _("New Zealand")),
    ("NI", _("Nicaragua")),
    ("NE", _("Niger")),
    ("NG", _("Nigeria")),
    ("NU", _("Niue")),
    ("NF", _("Norfolk Island")),
    ("MP", _("Northern Mariana Islands")),
    ("NO", _("Norway")),
    ("OM", _("Oman")),
    ("PK", _("Pakistan")),
    ("PW", _("Palau")),
    ("PS", _("Palestinian Territory, Occupied")),
    ("PA", _("Panama")),
    ("PG", _("Papua New Guinea")),
    ("PY", _("Paraguay")),
    ("PE", _("Peru")),
    ("PH", _("Philippines")),
    ("PN", _("Pitcairn")),
    ("PL", _("Poland")),
    ("PT", _("Portugal")),
    ("PR", _("Puerto Rico")),
    ("QA", _("Qatar")),
    ("RE", _("Reunion")),
    ("RO", _("Romania")),
    ("RU", _("Russian Federation")),
    ("RW", _("Rwanda")),
    ("BL", _("Saint Barthelemy")),
    ("SH", _("Saint Helena")),
    ("KN", _("Saint Kitts and Nevis")),
    ("LC", _("Saint Lucia")),
    ("MF", _("Saint Martin")),
    ("PM", _("Saint Pierre and Miquelon")),
    ("VC", _("Saint Vincent and the Grenadines")),
    ("WS", _("Samoa")),
    ("SM", _("San Marino")),
    ("ST", _("Sao Tome and Principe")),
    ("SA", _("Saudi Arabia")),
    ("SN", _("Senegal")),
    ("RS", _("Serbia")),
    ("SC", _("Seychelles")),
    ("SL", _("Sierra Leone")),
    ("SG", _("Singapore")),
    ("SK", _("Slovakia")),
    ("SI", _("Slovenia")),
    ("SB", _("Solomon Islands")),
    ("SO", _("Somalia")),
    ("ZA", _("South Africa")),
    ("GS", _("South Georgia and the South Sandwich Islands")),
    ("ES", _("Spain")),
    ("LK", _("Sri Lanka")),
    ("SD", _("Sudan")),
    ("SR", _("Suriname")),
    ("SJ", _("Svalbard and Jan Mayen")),
    ("SZ", _("Swaziland")),
    ("SE", _("Sweden")),
    ("CH", _("Switzerland")),
    ("SY", _("Syrian Arab Republic")),
    ("TW", _("Taiwan, Province of China")),
    ("TJ", _("Tajikistan")),
    ("TZ", _("Tanzania, United Republic of")),
    ("TH", _("Thailand")),
    ("TL", _("Timor-Leste")),
    ("TG", _("Togo")),
    ("TK", _("Tokelau")),
    ("TO", _("Tonga")),
    ("TT", _("Trinidad and Tobago")),
    ("TN", _("Tunisia")),
    ("TR", _("Turkey")),
    ("TM", _("Turkmenistan")),
    ("TC", _("Turks and Caicos Islands")),
    ("TV", _("Tuvalu")),
    ("UG", _("Uganda")),
    ("UA", _("Ukraine")),
    ("AE", _("United Arab Emirates")),
    ("US", _("United States")),
    ("UM", _("United States Minor Outlying Islands")),
    ("UY", _("Uruguay")),
    ("UZ", _("Uzbekistan")),
    ("VU", _("Vanuatu")),
    ("VE", _("Venezuela")),
    ("VN", _("Viet Nam")),
    ("VG", _("Virgin Islands, British")),
    ("VI", _("Virgin Islands, U.S.")),
    ("WF", _("Wallis and Futuna")),
    ("EH", _("Western Sahara")),
    ("YE", _("Yemen")),
    ("ZM", _("Zambia")),
    ("ZW", _("Zimbabwe")),
)

GOAL_TYPES = (
    ("REVENUE", "Revenue"),
    ("DEALS_CLOSED", "Deals Closed"),
    ("ACTIVITIES", "Activities"),
)

PERIOD_TYPES = (
    ("MONTHLY", "Monthly"),
    ("QUARTERLY", "Quarterly"),
    ("YEARLY", "Yearly"),
    ("CUSTOM", "Custom"),
)

CURRENCY_CODES = (
    ("USD", _("USD, Dollar")),
    ("EUR", _("EUR, Euro")),
    ("GBP", _("GBP, Pound")),
    ("INR", _("INR, Rupee")),
    ("CAD", _("CAD, Dollar")),
    ("AUD", _("AUD, Dollar")),
    ("JPY", _("JPY, Yen")),
    ("CNY", _("CNY, Yuan")),
    ("CHF", _("CHF, Franc")),
    ("SGD", _("SGD, Dollar")),
    ("AED", _("AED, Dirham")),
    ("BRL", _("BRL, Real")),
    ("MXN", _("MXN, Peso")),
)

CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "INR": "₹",
    "CAD": "CA$",
    "AUD": "A$",
    "JPY": "¥",
    "CNY": "¥",
    "CHF": "CHF",
    "SGD": "S$",
    "AED": "د.إ",
    "BRL": "R$",
    "MXN": "MX$",
}


def convert_to_custom_timezone(custom_date, custom_timezone, to_utc=False):
    user_time_zone = pytz.timezone(custom_timezone)
    if to_utc:
        custom_date = user_time_zone.localize(custom_date.replace(tzinfo=None))
        user_time_zone = pytz.UTC
    return custom_date.astimezone(user_time_zone)


# =============================================================================
# CRM Entity Utilities
# =============================================================================


def get_or_create_tags(tag_data, org):
    """
    Handle tag creation/lookup for any CRM model.

    Args:
        tag_data: List of tag names or JSON string of tag names
        org: Organization instance

    Returns:
        List of Tag objects
    """
    import json

    from common.models import Tags

    tags = []
    if not tag_data:
        return tags

    # Handle JSON string input
    if isinstance(tag_data, str):
        try:
            tag_data = json.loads(tag_data)
        except json.JSONDecodeError:
            tag_data = [tag_data]

    for tag_name in tag_data:
        if not tag_name:
            continue
        tag_obj, _ = Tags.objects.get_or_create(
            slug=tag_name.lower(), org=org, defaults={"name": tag_name}
        )
        tags.append(tag_obj)
    return tags


def handle_m2m_assignment(
    instance, field_name, ids, model_class, org, extra_filters=None
):
    """
    Handle ManyToMany field assignment with org filtering.

    Args:
        instance: Model instance with the M2M field
        field_name: Name of the M2M field
        ids: List of IDs or JSON string of IDs to assign
        model_class: The related model class
        org: Organization instance
        extra_filters: Optional dict of additional filters (e.g., {'is_active': True})

    Returns:
        QuerySet of objects that were assigned
    """
    import json

    field = getattr(instance, field_name)

    if not ids:
        return model_class.objects.none()

    # Handle JSON string input
    if isinstance(ids, str):
        try:
            ids = json.loads(ids)
        except json.JSONDecodeError:
            ids = [ids]

    filters = {"id__in": ids, "org": org}
    if extra_filters:
        filters.update(extra_filters)

    objects = model_class.objects.filter(**filters)
    if objects.exists():
        field.add(*objects)
    return objects


ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024


def fit_attachment_file_name(raw_name, max_length):
    """Squeeze an uploaded file's name into the label column, keeping the suffix.

    ``Attachments.file_name`` is 60 characters and ``Attachments.save()`` calls
    ``full_clean()``, so a longer name raised Django's ``ValidationError`` and
    the views turned it into a 500. Proven live before this existed: a 91
    character name, the shape an Android screenshot has by default
    (``IMG_20260807_100432_Screenshot_...jpg``), answered HTTP 500.

    Truncating rather than refusing, because the length of a file's name is not
    a user error and this column is a display label. The bytes are stored under
    the path ``FileField`` generates, which is unaffected.

    Any directory part is dropped too. Django sanitizes the storage path itself,
    but this label is echoed back to clients, and a name like
    ``../../../etc/passwd`` has no business being rendered as one.
    """
    name = (raw_name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name:
        return "attachment"
    if len(name) <= max_length:
        return name
    stem, _, suffix = name.rpartition(".")
    # A pathological "extension" must not eat the whole budget.
    suffix = f".{suffix}" if stem and len(suffix) < max_length // 2 else ""
    return (stem[: max_length - len(suffix)] + suffix)[:max_length]


def create_attachment(file, content_object, profile):
    """
    Create an attachment for any CRM content object.

    The one place an upload becomes a row. Every ``*_attachment`` endpoint had
    its own copy of these six lines, fifteen of them, so the filename defect
    above was fifteen defects and a size limit would have been fifteen edits.

    Args:
        file: The uploaded file object
        content_object: The model instance to attach to
        profile: The user's profile

    Returns:
        Attachments instance

    Raises:
        rest_framework.serializers.ValidationError: the file is over the size
            limit. DRF renders that as a 400 from any view, which is why it is
            raised here rather than returned: fifteen call sites would otherwise
            each need their own try/except, and the one that forgot would store
            the file anyway.
    """
    from rest_framework import serializers as drf_serializers

    from common.models import Attachments

    size = getattr(file, "size", None)
    if size is not None and size > ATTACHMENT_MAX_BYTES:
        megabytes = ATTACHMENT_MAX_BYTES // (1024 * 1024)
        raise drf_serializers.ValidationError(
            {"attachment": f"Files must be {megabytes} MB or smaller."}
        )

    attachment = Attachments()
    attachment.created_by = profile.user
    attachment.file_name = fit_attachment_file_name(
        file.name, Attachments._meta.get_field("file_name").max_length
    )
    attachment.content_object = content_object
    attachment.attachment = file
    attachment.org = profile.org
    attachment.save()
    return attachment


# =============================================================================
# Document File Type Utilities
# =============================================================================


def is_document_file_image(ext):
    image_ext_list = [
        "bmp",
        "dds",
        "gif",
        "jpg",
        "jpeg",
        "png",
        "psd",
        "pspimage",
        "tga",
        "thm",
        "tif",
        "tiff",
        "yuv",
    ]
    return ext.lower() in image_ext_list


def is_document_file_audio(ext):
    audio_ext_list = ["aif", "iff", "m3u", "m4a", "mid", "mp3", "mpa", "wav", "wma"]
    return ext.lower() in audio_ext_list


def is_document_file_video(ext):
    video_ext_list = [
        "3g2",
        "3gp",
        "asf",
        "avi",
        "flv",
        "m4v",
        "mov",
        "mp4",
        "mpg",
        "rm",
        "srt",
        "swf",
        "vob",
        "wmv",
    ]
    return ext.lower() in video_ext_list


def is_document_file_pdf(ext):
    pdf_ext_list = ["indd", "pct", "pdf"]
    return ext.lower() in pdf_ext_list


def is_document_file_code(ext):
    code_ext_list = [
        "aspx",
        "json",
        "jsp",
        "do",
        "htm",
        "html",
        "ser",
        "php",
        "jad",
        "cfm",
        "xml",
        "js",
        "pod",
        "asp",
        "atomsvc",
        "rdf",
        "pou",
        "jsf",
        "abs",
        "pl",
        "asm",
        "srz",
        "luac",
        "cod",
        "lib",
        "arxml",
        "bas",
        "ejs",
        "fs",
        "hbs",
        "s",
        "ss",
        "cms",
        "pyc",
        "vcxproj",
        "jse",
        "smali",
        "xla",
        "lxk",
        "pdb",
        "src",
        "cs",
        "ipb",
        "ave",
        "mst",
        "vls",
        "rcc",
        "sax",
        "scr",
        "dtd",
        "axd",
        "mrl",
        "xsl",
        "ino",
        "spr",
        "xsd",
        "cgi",
        "isa",
        "ws",
        "rss",
        "dvb",
        "nupkg",
        "xlm",
        "v4e",
        "prg",
        "form",
        "bat",
        "mrc",
        "asi",
        "jdp",
        "fmb",
        "graphml",
        "gcode",
        "aia",
        "py",
        "atp",
        "mzp",
        "o",
        "scs",
        "mm",
        "cpp",
        "java",
        "gypi",
        "idb",
        "txml",
        "c",
        "vip",
        "tra",
        "rc",
        "action",
        "vlx",
        "asta",
        "pyo",
        "lua",
        "gml",
        "prl",
        "rfs",
        "cpb",
        "sh",
        "rbf",
        "gp",
        "phtml",
        "bp",
        "scb",
        "sln",
        "vbp",
        "wbf",
        "bdt",
        "mac",
        "rpy",
        "eaf",
        "mc",
        "mwp",
        "gnt",
        "h",
        "swift",
        "e",
        "styl",
        "cxx",
        "as",
        "liquid",
        "dep",
        "fas",
        "vbs",
        "aps",
        "vbe",
        "lss",
        "cmake",
        "resx",
        "csb",
        "dpk",
        "pdml",
        "txx",
        "dbg",
        "jsa",
        "sxs",
        "sasf",
        "pm",
        "csx",
        "r",
        "wml",
        "au3",
        "stm",
        "cls",
        "cc",
        "ins",
        "jsc",
        "dwp",
        "rpg",
        "arb",
        "bml",
        "inc",
        "eld",
        "sct",
        "sm",
        "wbt",
        "csproj",
        "tcz",
        "html5",
        "gbl",
        "cmd",
        "dlg",
        "tpl",
        "rbt",
        "xcp",
        "tpm",
        "qry",
        "mfa",
        "ptx",
        "lsp",
        "pag",
        "ebc",
        "php3",
        "cob",
        "csc",
        "pyt",
        "dwt",
        "rb",
        "wsdl",
        "lap",
        "textile",
        "sfx",
        "x",
        "a5r",
        "dbp",
        "pmp",
        "ipr",
        "fwx",
        "pbl",
        "vbw",
        "phl",
        "cbl",
        "pas",
        "mom",
        "dbmdl",
        "lol",
        "wdl",
        "ppam",
        "plx",
        "vb",
        "cgx",
        "lst",
        "lmp",
        "vd",
        "bcp",
        "thtml",
        "scpt",
        "isu",
        "mrd",
        "perl",
        "dtx",
        "f",
        "wpk",
        "ipf",
        "ptl",
        "luca",
        "hx",
        "uvproj",
        "qvs",
        "vba",
        "xjb",
        "appxupload",
        "ti",
        "svn-base",
        "bsc",
        "mak",
        "vcproj",
        "dsd",
        "ksh",
        "pyw",
        "bxml",
        "mo",
        "irc",
        "gcl",
        "dbml",
        "mlv",
        "wsf",
        "tcl",
        "dqy",
        "ssi",
        "pbxproj",
        "bal",
        "trt",
        "sal",
        "hkp",
        "vbi",
        "dob",
        "htc",
        "p",
        "ats",
        "seam",
        "loc",
        "pli",
        "rptproj",
        "pxml",
        "pkb",
        "dpr",
        "scss",
        "dsb",
        "bb",
        "vbproj",
        "ash",
        "rml",
        "nbk",
        "nvi",
        "lmv",
        "mw",
        "jl",
        "dso",
        "cba",
        "jks",
        "ary",
        "run",
        "vps",
        "clm",
        "brml",
        "msha",
        "mdp",
        "tmh",
        "jsx",
        "sdl",
        "ptxml",
        "fxl",
        "wmw",
        "dcr",
        "bcc",
        "cbp",
        "bmo",
        "bsv",
        "less",
        "gss",
        "ctl",
        "rpyc",
        "ascx",
        "odc",
        "wiki",
        "obr",
        "l",
        "axs",
        "bpr",
        "ppa",
        "rpo",
        "sqlproj",
        "smm",
        "dsr",
        "arq",
        "din",
        "jml",
        "jsonp",
        "ml",
        "rc2",
        "myapp",
        "cla",
        "xme",
        "obj",
        "jsdtscope",
        "gyp",
        "datasource",
        "cp",
        "rh",
        "lpx",
        "a2w",
        "ctp",
        "ulp",
        "nt",
        "script",
        "bxl",
        "gs",
        "xslt",
        "mg",
        "pch",
        "mhl",
        "zpd",
        "psm1",
        "asz",
        "m",
        "jacl",
        "pym",
        "rws",
        "acu",
        "ssq",
        "wxs",
        "coffee",
        "ncb",
        "akt",
        "pyx",
        "zero",
        "hs",
        "mkb",
        "tru",
        "xul",
        "mfl",
        "sca",
        "sbr",
        "master",
        "opv",
        "matlab",
        "sami",
        "agc",
        "slim",
        "tea",
        "m51",
        "mec",
        "asc",
        "gch",
        "enml",
        "kst",
        "jade",
        "dfb",
        "ips",
        "rgs",
        "vbx",
        "cspkg",
        "ncx",
        "brs",
        "wfs",
        "ifp",
        "nse",
        "xtx",
        "j",
        "cx",
        "ps1",
        "nas",
        "mk",
        "ccs",
        "vrp",
        "lnp",
        "cml",
        "c#",
        "idl",
        "exp",
        "apb",
        "nsi",
        "asmx",
        "tdo",
        "pjt",
        "fdt",
        "s5d",
        "mvba",
        "mf",
        "odl",
        "bzs",
        "jardesc",
        "tgml",
        "moc",
        "wxi",
        "cpz",
        "fsx",
        "jav",
        "ocb",
        "agi",
        "tec",
        "txl",
        "amw",
        "mscr",
        "dfd",
        "dpd",
        "pun",
        "f95",
        "vdproj",
        "xsc",
        "diff",
        "wxl",
        "dgml",
        "airi",
        "kmt",
        "ksc",
        "io",
        "rbw",
        "sas",
        "vcp",
        "resources",
        "param",
        "cg",
        "hlsl",
        "vssscc",
        "bgm",
        "xn",
        "targets",
        "sl",
        "gsc",
        "qs",
        "owl",
        "devpak",
        "phps",
        "hdf",
        "pri",
        "nbin",
        "xaml",
        "s4e",
        "scm",
        "tk",
        "poc",
        "uix",
        "clw",
        "factorypath",
        "s43",
        "awd",
        "htr",
        "php2",
        "classpath",
        "pickle",
        "rob",
        "msil",
        "ebx",
        "tsq",
        "lml",
        "f90",
        "lds",
        "vup",
        "pbi",
        "swt",
        "vap",
        "ig",
        "pdo",
        "frt",
        "fcg",
        "c++",
        "xcl",
        "dfn",
        "aar",
        "for",
        "re",
        "twig",
        "ebm",
        "dhtml",
        "hc",
        "pro",
        "ahk",
        "rule",
        "bsh",
        "jcs",
        "zrx",
        "wsdd",
        "csp",
        "drc",
        "appxsym",
    ]
    return ext.lower() in code_ext_list


def is_document_file_text(ext):
    text_ext_list = [
        "doc",
        "docx",
        "log",
        "msg",
        "odt",
        "pages",
        "rtf",
        "tex",
        "txt",
        "wpd",
        "wps",
    ]
    return ext.lower() in text_ext_list


def is_document_file_sheet(ext):
    sheet_ext_list = ["csv", "xls", "xlsx", "xlsm", "xlsb", "xltx", "xltm", "xlt"]
    return ext.lower() in sheet_ext_list


def is_document_file_zip(ext):
    ext_list = ["zip", "7z", "gz", "rar", "zipx", "ace", "tar"]
    return ext.lower() in ext_list
