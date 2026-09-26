import 'package:flutter/foundation.dart' show setEquals;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import '../../core/theme/theme.dart';
import '../../data/models/models.dart';
import '../../providers/auth_provider.dart';
import '../../providers/leads_provider.dart';
import '../../providers/lookup_provider.dart';
import '../../widgets/common/common.dart';
import '../../widgets/forms/unsaved_changes.dart';

// Salutation choices match the web form's hard-coded list and the backend's
// LEAD_SALUTATION column (free-text, but the UI restricts to these values).
const List<String> _kSalutations = ['Mr.', 'Mrs.', 'Ms.', 'Dr.', 'Prof.'];

// Currencies match the frontend's CURRENCY_CODES constant; backend column is
// a 3-letter ISO code with the same `choices`.
const List<({String code, String label})> _kCurrencies = [
  (code: 'USD', label: 'USD - Dollar'),
  (code: 'EUR', label: 'EUR - Euro'),
  (code: 'GBP', label: 'GBP - Pound'),
  (code: 'INR', label: 'INR - Rupee'),
  (code: 'CAD', label: 'CAD - Dollar'),
  (code: 'AUD', label: 'AUD - Dollar'),
  (code: 'JPY', label: 'JPY - Yen'),
  (code: 'CNY', label: 'CNY - Yuan'),
  (code: 'CHF', label: 'CHF - Franc'),
  (code: 'SGD', label: 'SGD - Dollar'),
  (code: 'AED', label: 'AED - Dirham'),
  (code: 'BRL', label: 'BRL - Real'),
  (code: 'MXN', label: 'MXN - Peso'),
];

// Industries mirror the backend's INDCHOICES (common/utils.py). Keeping these
// in sync prevents free-text drift (Tech / Technology / tech) across orgs.
const List<String> _kIndustries = [
  'ADVERTISING',
  'AGRICULTURE',
  'APPAREL & ACCESSORIES',
  'AUTOMOTIVE',
  'BANKING',
  'BIOTECHNOLOGY',
  'BUILDING MATERIALS & EQUIPMENT',
  'CHEMICAL',
  'COMPUTER',
  'EDUCATION',
  'ELECTRONICS',
  'ENERGY',
  'ENTERTAINMENT & LEISURE',
  'FINANCE',
  'FOOD & BEVERAGE',
  'GROCERY',
  'HEALTHCARE',
  'INSURANCE',
  'LEGAL',
  'MANUFACTURING',
  'PUBLISHING',
  'REAL ESTATE',
  'SERVICE',
  'SOFTWARE',
  'SPORTS',
  'TECHNOLOGY',
  'TELECOMMUNICATIONS',
  'TELEVISION',
  'TRANSPORTATION',
  'VENTURE CAPITAL',
];

// Country picker options, backend stores the ISO-2/3 `code` and validates
// against this same list (common/utils.py COUNTRIES).
const List<({String code, String label})> _kCountries = [
  (code: 'GB', label: 'United Kingdom'),
  (code: 'AF', label: 'Afghanistan'),
  (code: 'AX', label: 'Aland Islands'),
  (code: 'AL', label: 'Albania'),
  (code: 'DZ', label: 'Algeria'),
  (code: 'AS', label: 'American Samoa'),
  (code: 'AD', label: 'Andorra'),
  (code: 'AO', label: 'Angola'),
  (code: 'AI', label: 'Anguilla'),
  (code: 'AQ', label: 'Antarctica'),
  (code: 'AG', label: 'Antigua and Barbuda'),
  (code: 'AR', label: 'Argentina'),
  (code: 'AM', label: 'Armenia'),
  (code: 'AW', label: 'Aruba'),
  (code: 'AU', label: 'Australia'),
  (code: 'AT', label: 'Austria'),
  (code: 'AZ', label: 'Azerbaijan'),
  (code: 'BS', label: 'Bahamas'),
  (code: 'BH', label: 'Bahrain'),
  (code: 'BD', label: 'Bangladesh'),
  (code: 'BB', label: 'Barbados'),
  (code: 'BY', label: 'Belarus'),
  (code: 'BE', label: 'Belgium'),
  (code: 'BZ', label: 'Belize'),
  (code: 'BJ', label: 'Benin'),
  (code: 'BM', label: 'Bermuda'),
  (code: 'BT', label: 'Bhutan'),
  (code: 'BO', label: 'Bolivia'),
  (code: 'BA', label: 'Bosnia and Herzegovina'),
  (code: 'BW', label: 'Botswana'),
  (code: 'BV', label: 'Bouvet Island'),
  (code: 'BR', label: 'Brazil'),
  (code: 'IO', label: 'British Indian Ocean Territory'),
  (code: 'BN', label: 'Brunei Darussalam'),
  (code: 'BG', label: 'Bulgaria'),
  (code: 'BF', label: 'Burkina Faso'),
  (code: 'BI', label: 'Burundi'),
  (code: 'KH', label: 'Cambodia'),
  (code: 'CM', label: 'Cameroon'),
  (code: 'CA', label: 'Canada'),
  (code: 'CV', label: 'Cape Verde'),
  (code: 'KY', label: 'Cayman Islands'),
  (code: 'CF', label: 'Central African Republic'),
  (code: 'TD', label: 'Chad'),
  (code: 'CL', label: 'Chile'),
  (code: 'CN', label: 'China'),
  (code: 'CX', label: 'Christmas Island'),
  (code: 'CC', label: 'Cocos (Keeling) Islands'),
  (code: 'CO', label: 'Colombia'),
  (code: 'KM', label: 'Comoros'),
  (code: 'CG', label: 'Congo'),
  (code: 'CD', label: 'Congo, The Democratic Republic of the'),
  (code: 'CK', label: 'Cook Islands'),
  (code: 'CR', label: 'Costa Rica'),
  (code: 'CI', label: "Cote d'Ivoire"),
  (code: 'HR', label: 'Croatia'),
  (code: 'CU', label: 'Cuba'),
  (code: 'CY', label: 'Cyprus'),
  (code: 'CZ', label: 'Czech Republic'),
  (code: 'DK', label: 'Denmark'),
  (code: 'DJ', label: 'Djibouti'),
  (code: 'DM', label: 'Dominica'),
  (code: 'DO', label: 'Dominican Republic'),
  (code: 'EC', label: 'Ecuador'),
  (code: 'EG', label: 'Egypt'),
  (code: 'SV', label: 'El Salvador'),
  (code: 'GQ', label: 'Equatorial Guinea'),
  (code: 'ER', label: 'Eritrea'),
  (code: 'EE', label: 'Estonia'),
  (code: 'ET', label: 'Ethiopia'),
  (code: 'FK', label: 'Falkland Islands (Malvinas)'),
  (code: 'FO', label: 'Faroe Islands'),
  (code: 'FJ', label: 'Fiji'),
  (code: 'FI', label: 'Finland'),
  (code: 'FR', label: 'France'),
  (code: 'GF', label: 'French Guiana'),
  (code: 'PF', label: 'French Polynesia'),
  (code: 'TF', label: 'French Southern Territories'),
  (code: 'GA', label: 'Gabon'),
  (code: 'GM', label: 'Gambia'),
  (code: 'GE', label: 'Georgia'),
  (code: 'DE', label: 'Germany'),
  (code: 'GH', label: 'Ghana'),
  (code: 'GI', label: 'Gibraltar'),
  (code: 'GR', label: 'Greece'),
  (code: 'GL', label: 'Greenland'),
  (code: 'GD', label: 'Grenada'),
  (code: 'GP', label: 'Guadeloupe'),
  (code: 'GU', label: 'Guam'),
  (code: 'GT', label: 'Guatemala'),
  (code: 'GG', label: 'Guernsey'),
  (code: 'GN', label: 'Guinea'),
  (code: 'GW', label: 'Guinea-Bissau'),
  (code: 'GY', label: 'Guyana'),
  (code: 'HT', label: 'Haiti'),
  (code: 'HM', label: 'Heard Island and McDonald Islands'),
  (code: 'VA', label: 'Holy See (Vatican City State)'),
  (code: 'HN', label: 'Honduras'),
  (code: 'HK', label: 'Hong Kong'),
  (code: 'HU', label: 'Hungary'),
  (code: 'IS', label: 'Iceland'),
  (code: 'IN', label: 'India'),
  (code: 'ID', label: 'Indonesia'),
  (code: 'IR', label: 'Iran, Islamic Republic of'),
  (code: 'IQ', label: 'Iraq'),
  (code: 'IE', label: 'Ireland'),
  (code: 'IM', label: 'Isle of Man'),
  (code: 'IL', label: 'Israel'),
  (code: 'IT', label: 'Italy'),
  (code: 'JM', label: 'Jamaica'),
  (code: 'JP', label: 'Japan'),
  (code: 'JE', label: 'Jersey'),
  (code: 'JO', label: 'Jordan'),
  (code: 'KZ', label: 'Kazakhstan'),
  (code: 'KE', label: 'Kenya'),
  (code: 'KI', label: 'Kiribati'),
  (code: 'KP', label: "Korea, Democratic People's Republic of"),
  (code: 'KR', label: 'Korea, Republic of'),
  (code: 'KW', label: 'Kuwait'),
  (code: 'KG', label: 'Kyrgyzstan'),
  (code: 'LA', label: "Lao People's Democratic Republic"),
  (code: 'LV', label: 'Latvia'),
  (code: 'LB', label: 'Lebanon'),
  (code: 'LS', label: 'Lesotho'),
  (code: 'LR', label: 'Liberia'),
  (code: 'LY', label: 'Libyan Arab Jamahiriya'),
  (code: 'LI', label: 'Liechtenstein'),
  (code: 'LT', label: 'Lithuania'),
  (code: 'LU', label: 'Luxembourg'),
  (code: 'MO', label: 'Macao'),
  (code: 'MK', label: 'Macedonia, The Former Yugoslav Republic of'),
  (code: 'MG', label: 'Madagascar'),
  (code: 'MW', label: 'Malawi'),
  (code: 'MY', label: 'Malaysia'),
  (code: 'MV', label: 'Maldives'),
  (code: 'ML', label: 'Mali'),
  (code: 'MT', label: 'Malta'),
  (code: 'MH', label: 'Marshall Islands'),
  (code: 'MQ', label: 'Martinique'),
  (code: 'MR', label: 'Mauritania'),
  (code: 'MU', label: 'Mauritius'),
  (code: 'YT', label: 'Mayotte'),
  (code: 'MX', label: 'Mexico'),
  (code: 'FM', label: 'Micronesia, Federated States of'),
  (code: 'MD', label: 'Moldova'),
  (code: 'MC', label: 'Monaco'),
  (code: 'MN', label: 'Mongolia'),
  (code: 'ME', label: 'Montenegro'),
  (code: 'MS', label: 'Montserrat'),
  (code: 'MA', label: 'Morocco'),
  (code: 'MZ', label: 'Mozambique'),
  (code: 'MM', label: 'Myanmar'),
  (code: 'NA', label: 'Namibia'),
  (code: 'NR', label: 'Nauru'),
  (code: 'NP', label: 'Nepal'),
  (code: 'NL', label: 'Netherlands'),
  (code: 'AN', label: 'Netherlands Antilles'),
  (code: 'NC', label: 'New Caledonia'),
  (code: 'NZ', label: 'New Zealand'),
  (code: 'NI', label: 'Nicaragua'),
  (code: 'NE', label: 'Niger'),
  (code: 'NG', label: 'Nigeria'),
  (code: 'NU', label: 'Niue'),
  (code: 'NF', label: 'Norfolk Island'),
  (code: 'MP', label: 'Northern Mariana Islands'),
  (code: 'NO', label: 'Norway'),
  (code: 'OM', label: 'Oman'),
  (code: 'PK', label: 'Pakistan'),
  (code: 'PW', label: 'Palau'),
  (code: 'PS', label: 'Palestinian Territory, Occupied'),
  (code: 'PA', label: 'Panama'),
  (code: 'PG', label: 'Papua New Guinea'),
  (code: 'PY', label: 'Paraguay'),
  (code: 'PE', label: 'Peru'),
  (code: 'PH', label: 'Philippines'),
  (code: 'PN', label: 'Pitcairn'),
  (code: 'PL', label: 'Poland'),
  (code: 'PT', label: 'Portugal'),
  (code: 'PR', label: 'Puerto Rico'),
  (code: 'QA', label: 'Qatar'),
  (code: 'RE', label: 'Reunion'),
  (code: 'RO', label: 'Romania'),
  (code: 'RU', label: 'Russian Federation'),
  (code: 'RW', label: 'Rwanda'),
  (code: 'BL', label: 'Saint Barthelemy'),
  (code: 'SH', label: 'Saint Helena'),
  (code: 'KN', label: 'Saint Kitts and Nevis'),
  (code: 'LC', label: 'Saint Lucia'),
  (code: 'MF', label: 'Saint Martin'),
  (code: 'PM', label: 'Saint Pierre and Miquelon'),
  (code: 'VC', label: 'Saint Vincent and the Grenadines'),
  (code: 'WS', label: 'Samoa'),
  (code: 'SM', label: 'San Marino'),
  (code: 'ST', label: 'Sao Tome and Principe'),
  (code: 'SA', label: 'Saudi Arabia'),
  (code: 'SN', label: 'Senegal'),
  (code: 'RS', label: 'Serbia'),
  (code: 'SC', label: 'Seychelles'),
  (code: 'SL', label: 'Sierra Leone'),
  (code: 'SG', label: 'Singapore'),
  (code: 'SK', label: 'Slovakia'),
  (code: 'SI', label: 'Slovenia'),
  (code: 'SB', label: 'Solomon Islands'),
  (code: 'SO', label: 'Somalia'),
  (code: 'ZA', label: 'South Africa'),
  (code: 'GS', label: 'South Georgia and the South Sandwich Islands'),
  (code: 'ES', label: 'Spain'),
  (code: 'LK', label: 'Sri Lanka'),
  (code: 'SD', label: 'Sudan'),
  (code: 'SR', label: 'Suriname'),
  (code: 'SJ', label: 'Svalbard and Jan Mayen'),
  (code: 'SZ', label: 'Swaziland'),
  (code: 'SE', label: 'Sweden'),
  (code: 'CH', label: 'Switzerland'),
  (code: 'SY', label: 'Syrian Arab Republic'),
  (code: 'TW', label: 'Taiwan, Province of China'),
  (code: 'TJ', label: 'Tajikistan'),
  (code: 'TZ', label: 'Tanzania, United Republic of'),
  (code: 'TH', label: 'Thailand'),
  (code: 'TL', label: 'Timor-Leste'),
  (code: 'TG', label: 'Togo'),
  (code: 'TK', label: 'Tokelau'),
  (code: 'TO', label: 'Tonga'),
  (code: 'TT', label: 'Trinidad and Tobago'),
  (code: 'TN', label: 'Tunisia'),
  (code: 'TR', label: 'Turkey'),
  (code: 'TM', label: 'Turkmenistan'),
  (code: 'TC', label: 'Turks and Caicos Islands'),
  (code: 'TV', label: 'Tuvalu'),
  (code: 'UG', label: 'Uganda'),
  (code: 'UA', label: 'Ukraine'),
  (code: 'AE', label: 'United Arab Emirates'),
  (code: 'US', label: 'United States'),
  (code: 'UM', label: 'United States Minor Outlying Islands'),
  (code: 'UY', label: 'Uruguay'),
  (code: 'UZ', label: 'Uzbekistan'),
  (code: 'VU', label: 'Vanuatu'),
  (code: 'VE', label: 'Venezuela'),
  (code: 'VN', label: 'Viet Nam'),
  (code: 'VG', label: 'Virgin Islands, British'),
  (code: 'VI', label: 'Virgin Islands, U.S.'),
  (code: 'WF', label: 'Wallis and Futuna'),
  (code: 'EH', label: 'Western Sahara'),
  (code: 'YE', label: 'Yemen'),
  (code: 'ZM', label: 'Zambia'),
  (code: 'ZW', label: 'Zimbabwe'),
];

String? _countryLabel(String? code) {
  if (code == null || code.isEmpty) return null;
  for (final c in _kCountries) {
    if (c.code == code) return c.label;
  }
  return code;
}

// Status → default probability % that gets pre-filled when the user picks a
// status and hasn't typed one yet. Matches the web's auto-suggest behavior.
int _suggestedProbabilityForStatus(LeadStatus status) {
  switch (status) {
    case LeadStatus.assigned:
      return 10;
    case LeadStatus.inProcess:
      return 50;
    case LeadStatus.converted:
      return 100;
    case LeadStatus.recycled:
      return 25;
    case LeadStatus.closed:
      return 0;
  }
}

// Status icons, match the source picker affordance so every classification
// row has a consistent visual leading element.
IconData _statusIcon(LeadStatus s) {
  switch (s) {
    case LeadStatus.assigned:
      return LucideIcons.userCheck;
    case LeadStatus.inProcess:
      return LucideIcons.activity;
    case LeadStatus.converted:
      return LucideIcons.checkCircle;
    case LeadStatus.recycled:
      return LucideIcons.refreshCw;
    case LeadStatus.closed:
      return LucideIcons.xCircle;
  }
}

final _emailRegex = RegExp(r'^[\w.+-]+@[\w-]+(\.[\w-]+)+$');
final _phoneAllowed = RegExp(r'[0-9+\-\s().]');

/// Lead Form Screen - Reusable for both Create and Edit
class LeadFormScreen extends ConsumerStatefulWidget {
  final String? leadId;
  final Lead? initialLead;

  const LeadFormScreen({super.key, this.leadId, this.initialLead});

  bool get isEditMode => leadId != null;

  @override
  ConsumerState<LeadFormScreen> createState() => _LeadFormScreenState();
}

class _LeadFormScreenState extends ConsumerState<LeadFormScreen> {
  final _formKey = GlobalKey<FormState>();
  final _scrollController = ScrollController();

  // --- Identity ---
  String? _salutation;
  final _firstNameController = TextEditingController();
  final _firstNameKey = GlobalKey();
  final _lastNameController = TextEditingController();
  final _lastNameKey = GlobalKey();
  final _titleController = TextEditingController();
  final _titleKey = GlobalKey();

  // --- Contact ---
  final _emailController = TextEditingController();
  final _emailKey = GlobalKey();
  final _phoneController = TextEditingController();
  final _phoneKey = GlobalKey();
  final _linkedinController = TextEditingController();
  final _linkedinKey = GlobalKey();

  // --- Company ---
  final _companyController = TextEditingController();
  final _companyKey = GlobalKey();
  final _jobTitleController = TextEditingController();
  final _websiteController = TextEditingController();
  final _websiteKey = GlobalKey();
  final _industryController = TextEditingController();
  final _industryKey = GlobalKey();

  // --- Classification & Deal ---
  LeadStatus _status = LeadStatus.assigned;
  LeadSource? _source; // nullable; backend accepts blank, not empty string
  // Nullable to mirror the backend (rating column is blank=True). Form starts
  // unset; user explicitly picks Cold/Warm/Hot or leaves it.
  LeadRating? _rating;
  final _opportunityAmountController = TextEditingController();
  final _opportunityAmountKey = GlobalKey();
  String? _currency;
  final _probabilityController = TextEditingController();
  final _probabilityKey = GlobalKey();
  DateTime? _closeDate;

  // --- Address ---
  final _addressLineController = TextEditingController();
  final _cityController = TextEditingController();
  final _stateController = TextEditingController();
  final _postcodeController = TextEditingController();
  final _countryController = TextEditingController();

  // --- Activity ---
  DateTime? _lastContacted;
  DateTime? _nextFollowUp;

  // --- Notes ---
  final _notesController = TextEditingController();

  // --- Relationships ---
  List<String> _assignedToIds = [];
  List<String> _tagIds = [];

  // --- Custom Fields ---
  List<CustomFieldDefinition> _customFieldDefs = const [];
  Map<String, dynamic> _customFieldValues = {};
  // Reused controllers for text/number/textarea custom fields, keyed by
  // definition key. We keep them on state so typing doesn't lose focus
  // between rebuilds.
  final Map<String, TextEditingController> _customFieldControllers = {};

  bool _isLoading = false;
  bool _isFetchingLead = false;
  String? _fetchError;
  Lead? _existingLead;

  // Sticky-section quick-jump targets. Populated when each section is built.
  final _identitySectionKey = GlobalKey();
  final _contactSectionKey = GlobalKey();
  final _companySectionKey = GlobalKey();
  final _pipelineSectionKey = GlobalKey();
  final _addressSectionKey = GlobalKey();
  final _activitySectionKey = GlobalKey();
  final _relationshipsSectionKey = GlobalKey();
  final _customFieldsSectionKey = GlobalKey();
  final _notesSectionKey = GlobalKey();

  // Duplicate-email check: hint text + link to existing record. Only set in
  // create mode after the email field blurs with a non-empty value.
  final _emailFocusNode = FocusNode();
  String? _duplicateLeadId;
  String? _duplicateLeadLabel;
  // True while the duplicate-check API call is in flight.
  bool _checkingDuplicate = false;
  // Last email we sent to the duplicate-check endpoint, to debounce repeated
  // checks when the user blurs/refocuses without editing.
  String? _lastCheckedEmail;

  // "Save & Add Another", when true, on successful submit we reset the form
  // instead of popping back to the previous screen.
  bool _saveAndAddAnother = false;

  @override
  void initState() {
    super.initState();

    if (widget.isEditMode) {
      // Always re-fetch the full detail in edit mode. `initialLead` (when
      // present from the list-row stub) is missing address/deal/custom-field
      // data, so trusting it would let the user silently blank those fields.
      if (widget.initialLead != null) {
        _populateFromLead(widget.initialLead!);
        // A baseline from the stub, so a fetch that fails still leaves the
        // form comparable against something the user can see.
        _baseline = _snapshot();
      }
      _fetchLead();
    } else {
      _applyCreateModeDefaults();
      _baseline = _snapshot();
      // Custom fields aren't part of the create form's payload by default;
      // load them lazily after first frame so the schema picker can render.
      WidgetsBinding.instance.addPostFrameCallback((_) {
        _loadCustomFieldDefsForCreate();
      });
    }

    _emailFocusNode.addListener(_onEmailFocusChange);
  }

  /// Populate sensible defaults for a brand-new lead so the user doesn't have
  /// to pick currency, close date, and assignee manually for every lead.
  void _applyCreateModeDefaults() {
    final org = ref.read(selectedOrgProvider);
    _currency = org?.defaultCurrency;
    // 30-day close date is a common heuristic for fresh leads; user can
    // override (or clear).
    _closeDate = DateTime.now().add(const Duration(days: 30));

    // Self-assign: find the current user's profile in the users lookup and
    // pre-select it. Falls back to empty if the lookup hasn't loaded yet.
    final me = ref.read(currentUserProvider);
    if (me != null) {
      final users = ref.read(usersProvider);
      final myEmail = me.email.toLowerCase();
      for (final u in users) {
        if (u.email.toLowerCase() == myEmail) {
          _assignedToIds = [u.id];
          break;
        }
      }
    }
  }

  Future<void> _loadCustomFieldDefsForCreate() async {
    try {
      final defs = await ref.read(
        customFieldDefinitionsProvider('Lead').future,
      );
      if (!mounted) return;
      setState(() {
        _customFieldDefs = defs;
        _seedCustomFieldControllers();
      });
    } catch (_) {
      // Soft-fail: custom fields are optional and not required to create a
      // lead. The submit will simply omit them.
    }
  }

  @override
  void dispose() {
    _firstNameController.dispose();
    _lastNameController.dispose();
    _titleController.dispose();
    _emailController.dispose();
    _phoneController.dispose();
    _linkedinController.dispose();
    _companyController.dispose();
    _jobTitleController.dispose();
    _websiteController.dispose();
    _industryController.dispose();
    _opportunityAmountController.dispose();
    _probabilityController.dispose();
    _addressLineController.dispose();
    _cityController.dispose();
    _stateController.dispose();
    _postcodeController.dispose();
    _countryController.dispose();
    _notesController.dispose();
    for (final c in _customFieldControllers.values) {
      c.dispose();
    }
    _scrollController.dispose();
    _emailFocusNode.removeListener(_onEmailFocusChange);
    _emailFocusNode.dispose();
    super.dispose();
  }

  Future<void> _fetchLead() async {
    setState(() {
      _isFetchingLead = true;
      _fetchError = null;
    });

    final detail = await ref
        .read(leadsProvider.notifier)
        .getLeadDetail(widget.leadId!);

    if (!mounted) return;
    setState(() {
      _isFetchingLead = false;
      if (detail != null) {
        _existingLead = detail.lead;
        _customFieldDefs = detail.customFieldDefinitions;
        _populateFromLead(detail.lead);
        _seedCustomFieldControllers();
        _baseline = _snapshot();
      } else {
        _fetchError = 'Failed to load lead';
      }
    });
  }

  void _populateFromLead(Lead lead) {
    _existingLead = lead;
    _salutation = lead.salutation;
    _firstNameController.text = lead.firstName;
    _lastNameController.text = lead.lastName;
    _titleController.text = lead.title ?? '';
    _emailController.text = lead.email;
    _phoneController.text = lead.phone ?? '';
    _linkedinController.text = lead.linkedinUrl ?? '';
    _companyController.text = lead.companyName;
    _jobTitleController.text = lead.jobTitle ?? '';
    _websiteController.text = lead.website ?? '';
    _industryController.text = lead.industry ?? '';
    _status = lead.status;
    _source = lead.source == LeadSource.none ? null : lead.source;
    _rating = lead.rating;
    _opportunityAmountController.text = lead.opportunityAmount != null
        ? _formatAmount(lead.opportunityAmount!)
        : '';
    _currency = lead.currency ?? ref.read(selectedOrgProvider)?.defaultCurrency;
    _probabilityController.text = lead.probability != null
        ? lead.probability.toString()
        : '';
    _closeDate = lead.closeDate;
    _addressLineController.text = lead.addressLine ?? '';
    _cityController.text = lead.city ?? '';
    _stateController.text = lead.state ?? '';
    _postcodeController.text = lead.postcode ?? '';
    _countryController.text = lead.country ?? '';
    _lastContacted = lead.lastContacted;
    _nextFollowUp = lead.nextFollowUp;
    _notesController.text = lead.description ?? '';
    _assignedToIds = List.from(lead.assignedToIds);
    _tagIds = List.from(lead.tagIds);
    _customFieldValues = Map<String, dynamic>.from(lead.customFieldValues);
  }

  void _seedCustomFieldControllers() {
    for (final def in _customFieldDefs) {
      if (def.fieldType == CustomFieldType.text ||
          def.fieldType == CustomFieldType.textarea ||
          def.fieldType == CustomFieldType.number) {
        final existing = _customFieldControllers[def.key];
        final raw = _customFieldValues[def.key];
        final text = raw == null ? '' : raw.toString();
        if (existing == null) {
          _customFieldControllers[def.key] = TextEditingController(text: text);
        } else if (existing.text != text) {
          existing.text = text;
        }
      }
    }
  }

  String _formatAmount(double v) {
    // Drop trailing zeros / decimal point for whole numbers so the input
    // shows "5000" instead of "5000.0".
    if (v == v.truncateToDouble()) return v.toStringAsFixed(0);
    return v.toString();
  }

  /// The form as it looked when it finished loading, against the payload it
  /// would submit now.
  ///
  /// The hand-listed comparison this replaces checked ten of the create
  /// form's fields, so typing only into one of the other thirteen left
  /// without a prompt. Comparing the payload means a field cannot be
  /// forgotten: if it is submitted, it counts.
  String? _baseline;

  /// `toString` rather than `jsonEncode`, which throws on the DateTime and
  /// enum values these payloads carry. Key order is stable because the same
  /// builder produces every snapshot.
  String _snapshot() => _buildPayload().toString();

  bool get _hasUnsavedChanges => _baseline != null && _snapshot() != _baseline;

  Map<String, dynamic> _buildPayload() {
    final payload = <String, dynamic>{
      'first_name': _firstNameController.text.trim(),
      'last_name': _lastNameController.text.trim(),
      'email': _emailController.text.trim(),
      'company_name': _companyController.text.trim(),
    };

    // On an edit, status goes only when it changed. `LeadCreateSerializer
    // .validate_status` refuses ANY status on a converted lead, the same value
    // included (re-sending "converted" would convert it a second time), so
    // always sending it made every other field of a converted lead uneditable
    // from the phone. The web omits it when unchanged too.
    if (!widget.isEditMode || _status != _existingLead?.status) {
      payload['status'] = _status.value;
    }

    // Rating is nullable on the backend. Send the chosen value, or explicitly
    // null on edit when the user cleared it (so the column gets cleared too).
    if (_rating != null) {
      payload['rating'] = _rating!.value;
    } else if (widget.isEditMode) {
      payload['rating'] = null;
    }

    // Source is optional. Send only when set; backend's source column is
    // nullable and rejects the empty string ''.
    if (_source != null) {
      payload['source'] = _source!.value;
    } else if (widget.isEditMode) {
      payload['source'] = null;
    }

    void putOptionalString(String key, String value) {
      final trimmed = value.trim();
      if (trimmed.isNotEmpty || widget.isEditMode) {
        payload[key] = trimmed.isEmpty ? null : trimmed;
      }
    }

    putOptionalString('title', _titleController.text);
    putOptionalString('salutation', _salutation ?? '');
    putOptionalString('job_title', _jobTitleController.text);
    putOptionalString('website', _websiteController.text);
    putOptionalString('linkedin_url', _linkedinController.text);
    putOptionalString('industry', _industryController.text);
    putOptionalString('description', _notesController.text);
    putOptionalString('address_line', _addressLineController.text);
    putOptionalString('city', _cityController.text);
    putOptionalString('state', _stateController.text);
    putOptionalString('postcode', _postcodeController.text);
    putOptionalString('country', _countryController.text);

    // Phone: backend validator rejects the empty string, so only include it
    // when non-empty even in edit mode (server treats missing key as
    // "leave unchanged").
    final phone = _phoneController.text.trim();
    if (phone.isNotEmpty) {
      payload['phone'] = phone;
    } else if (widget.isEditMode && (_existingLead?.phone ?? '').isNotEmpty) {
      // User cleared the phone in edit mode, null clears the column.
      payload['phone'] = null;
    }

    // Numbers & currency
    final amountText = _opportunityAmountController.text.trim();
    if (amountText.isNotEmpty) {
      final parsed = double.tryParse(amountText);
      if (parsed != null) payload['opportunity_amount'] = parsed;
    } else if (widget.isEditMode) {
      payload['opportunity_amount'] = null;
    }
    if (_currency != null && _currency!.isNotEmpty) {
      payload['currency'] = _currency;
    } else if (widget.isEditMode) {
      payload['currency'] = null;
    }
    final probText = _probabilityController.text.trim();
    if (probText.isNotEmpty) {
      final parsed = int.tryParse(probText);
      if (parsed != null) payload['probability'] = parsed;
    } else if (widget.isEditMode) {
      payload['probability'] = null;
    }

    // Dates
    payload['close_date'] = _formatDate(_closeDate);
    payload['last_contacted'] = _formatDate(_lastContacted);
    payload['next_follow_up'] = _formatDate(_nextFollowUp);

    // On an edit, each list goes only when its selection changed. Lead PATCH
    // leaves an absent list alone but replaces a present one with the ACTIVE
    // ids in it, and the pickers offer active people and tags only, so sending
    // both on every save dropped each archived tag and deactivated assignee
    // whatever the save was for. Sent, an empty list clears the M2M.
    if (!widget.isEditMode ||
        !setEquals(
          _assignedToIds.toSet(),
          _existingLead?.assignedToIds.toSet(),
        )) {
      payload['assigned_to'] = _assignedToIds;
    }
    if (!widget.isEditMode ||
        !setEquals(_tagIds.toSet(), _existingLead?.tagIds.toSet())) {
      payload['tags'] = _tagIds;
    }

    // Custom fields, only send when the org has defined any, to avoid
    // overwriting unrelated keys.
    if (_customFieldDefs.isNotEmpty) {
      payload['custom_fields'] = _customFieldValues;
    }

    return payload;
  }

  String? _formatDate(DateTime? d) {
    if (d == null) return null;
    return DateFormat('yyyy-MM-dd').format(d);
  }

  Future<void> _handleSubmit() async {
    if (!(_formKey.currentState?.validate() ?? false)) {
      _scrollToFirstError();
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: const Text('Please fix the errors in the form'),
          behavior: SnackBarBehavior.floating,
          backgroundColor: AppColors.danger600,
        ),
      );
      return;
    }

    // Custom-field required-check (the form validator can't easily reach
    // into the bespoke widgets we render for non-text types).
    for (final def in _customFieldDefs) {
      if (!def.isRequired) continue;
      final v = _customFieldValues[def.key];
      final missing = v == null || (v is String && v.trim().isEmpty);
      if (missing) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('${def.label} is required'),
            behavior: SnackBarBehavior.floating,
            backgroundColor: AppColors.danger600,
          ),
        );
        return;
      }
    }

    setState(() => _isLoading = true);

    final payload = _buildPayload();
    final notifier = ref.read(leadsProvider.notifier);
    final response = widget.isEditMode
        ? await notifier.updateLead(widget.leadId!, payload)
        : await notifier.createLead(payload);

    if (!mounted) return;
    setState(() => _isLoading = false);

    if (response.success) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            widget.isEditMode
                ? 'Lead updated successfully'
                : 'Lead created successfully',
          ),
          behavior: SnackBarBehavior.floating,
        ),
      );
      if (_saveAndAddAnother && !widget.isEditMode) {
        _resetFormForNextLead();
      } else {
        context.pop(true);
      }
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(response.message ?? 'Failed to save lead'),
          behavior: SnackBarBehavior.floating,
          backgroundColor: AppColors.danger600,
        ),
      );
    }
  }

  /// Reset the form to a pristine state for "Save & Add Another". We clear
  /// every user-entered field but re-apply the create-mode defaults so the
  /// next lead inherits the same currency/close-date/assignee baseline.
  void _resetFormForNextLead() {
    _formKey.currentState?.reset();
    setState(() {
      _salutation = null;
      _firstNameController.clear();
      _lastNameController.clear();
      _titleController.clear();
      _emailController.clear();
      _phoneController.clear();
      _linkedinController.clear();
      _companyController.clear();
      _jobTitleController.clear();
      _websiteController.clear();
      _industryController.clear();
      _status = LeadStatus.assigned;
      _source = null;
      _rating = null;
      _opportunityAmountController.clear();
      _probabilityController.clear();
      _addressLineController.clear();
      _cityController.clear();
      _stateController.clear();
      _postcodeController.clear();
      _countryController.clear();
      _lastContacted = null;
      _nextFollowUp = null;
      _notesController.clear();
      _assignedToIds = const [];
      _tagIds = const [];
      _customFieldValues = {};
      _duplicateLeadId = null;
      _duplicateLeadLabel = null;
      _lastCheckedEmail = null;
      _saveAndAddAnother = false;
      _applyCreateModeDefaults();
      _seedCustomFieldControllers();
    });
    _scrollController.animateTo(
      0,
      duration: const Duration(milliseconds: 300),
      curve: Curves.easeOut,
    );
  }

  void _scrollToFirstError() {
    // Order must mirror the visual render order so the scroll lands on the
    // FIRST failing field, not whichever happens to come earlier in the list.
    final orderedKeys = <GlobalKey>[
      _titleKey,
      _firstNameKey,
      _lastNameKey,
      _emailKey,
      _phoneKey,
      _linkedinKey,
      _companyKey,
      _websiteKey,
      _industryKey,
      _opportunityAmountKey,
      _probabilityKey,
    ];
    for (final key in orderedKeys) {
      final ctx = key.currentContext;
      if (ctx == null) continue;
      // Find a TextFormField state under this key, if its errorText is
      // populated, this is the first failing field.
      bool foundError = false;
      void visit(Element element) {
        if (foundError) return;
        if (element.widget is TextFormField) {
          final state = (element as StatefulElement).state;
          // FormFieldState has hasError.
          if (state is FormFieldState && state.hasError) {
            foundError = true;
          }
        }
        element.visitChildren(visit);
      }

      (ctx as Element).visitChildren(visit);
      if (foundError) {
        Scrollable.ensureVisible(
          ctx,
          alignment: 0.2,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
        return;
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return UnsavedChangesGuard(
      hasUnsavedChanges: () => _hasUnsavedChanges,
      isSaving: _isLoading,
      child: Scaffold(
        backgroundColor: AppColors.surface,
        appBar: AppBar(
          title: Text(widget.isEditMode ? 'Edit Lead' : 'New Lead'),
          backgroundColor: AppColors.surface,
          elevation: 0,
          scrolledUnderElevation: 1,
          leading: IconButton(
            icon: const Icon(LucideIcons.chevronLeft),
            onPressed: _isLoading
                ? null
                : () =>
                      leaveForm(context, hasUnsavedChanges: _hasUnsavedChanges),
          ),
        ),
        body: _buildBody(),
        bottomNavigationBar: _isFetchingLead || _fetchError != null
            ? null
            : _buildStickyBottomBar(),
      ),
    );
  }

  Widget _buildBody() {
    if (_isFetchingLead && _existingLead == null) {
      return const Center(child: CircularProgressIndicator());
    }

    if (_fetchError != null) {
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(LucideIcons.alertCircle, size: 48, color: AppColors.danger500),
            const SizedBox(height: 16),
            Text(
              _fetchError!,
              style: AppTypography.body.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(height: 16),
            TextButton(onPressed: _fetchLead, child: const Text('Retry')),
          ],
        ),
      );
    }

    return Form(
      key: _formKey,
      child: Column(
        children: [
          _buildSectionChipsBar(),
          Expanded(
            child: SingleChildScrollView(
              controller: _scrollController,
              padding: const EdgeInsets.fromLTRB(24, 24, 24, 32),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _buildSectionTitle('Identity'),
                  const SizedBox(height: 16),
                  _buildIdentitySection(),
                  const SizedBox(height: 32),
                  _buildSectionTitle('Contact'),
                  const SizedBox(height: 16),
                  _buildContactSection(),
                  const SizedBox(height: 32),
                  _buildSectionTitle('Company'),
                  const SizedBox(height: 16),
                  _buildCompanySection(),
                  const SizedBox(height: 32),
                  _buildSectionTitle('Pipeline & Deal'),
                  const SizedBox(height: 16),
                  _buildPipelineSection(),
                  const SizedBox(height: 32),
                  _buildSectionTitle('Address'),
                  const SizedBox(height: 16),
                  _buildAddressSection(),
                  const SizedBox(height: 32),
                  _buildSectionTitle('Activity Dates'),
                  const SizedBox(height: 16),
                  _buildActivitySection(),
                  const SizedBox(height: 32),
                  _buildSectionTitle('Relationships'),
                  const SizedBox(height: 16),
                  _buildRelationshipsSection(),
                  if (_customFieldDefs.isNotEmpty) ...[
                    const SizedBox(height: 32),
                    _buildSectionTitle('Custom Fields'),
                    const SizedBox(height: 16),
                    _buildCustomFieldsSection(),
                  ],
                  const SizedBox(height: 32),
                  KeyedSubtree(
                    key: _notesSectionKey,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        _buildSectionTitle('Notes'),
                        const SizedBox(height: 16),
                        TextAreaField(
                          label: 'Description',
                          hint: 'Add any additional notes about this lead...',
                          controller: _notesController,
                          maxLines: 4,
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 16),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  /// Horizontal pill strip pinned above the scroll view, taps scroll the form
  /// to that section. Helps when the form gets long; sections like Notes,
  /// Custom Fields, and Relationships are otherwise a long scroll away.
  Widget _buildSectionChipsBar() {
    final chips = <({String label, GlobalKey key, IconData icon})>[
      (label: 'Identity', key: _identitySectionKey, icon: LucideIcons.user),
      (label: 'Contact', key: _contactSectionKey, icon: LucideIcons.mail),
      (label: 'Company', key: _companySectionKey, icon: LucideIcons.building2),
      (label: 'Pipeline', key: _pipelineSectionKey, icon: LucideIcons.activity),
      (label: 'Address', key: _addressSectionKey, icon: LucideIcons.mapPin),
      (label: 'Activity', key: _activitySectionKey, icon: LucideIcons.calendar),
      (
        label: 'Relations',
        key: _relationshipsSectionKey,
        icon: LucideIcons.users,
      ),
      if (_customFieldDefs.isNotEmpty)
        (
          label: 'Custom',
          key: _customFieldsSectionKey,
          icon: LucideIcons.settings2,
        ),
      (label: 'Notes', key: _notesSectionKey, icon: LucideIcons.fileText),
    ];

    return Container(
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border(bottom: BorderSide(color: AppColors.border)),
      ),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        child: Row(
          children: [
            for (final chip in chips)
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: ActionChip(
                  avatar: Icon(chip.icon, size: 14),
                  label: Text(chip.label),
                  visualDensity: VisualDensity.compact,
                  materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  side: BorderSide(color: AppColors.border),
                  backgroundColor: AppColors.gray50,
                  labelStyle: AppTypography.caption.copyWith(
                    color: AppColors.textPrimary,
                    fontWeight: FontWeight.w500,
                  ),
                  onPressed: () => _jumpToSection(chip.key),
                ),
              ),
          ],
        ),
      ),
    );
  }

  void _jumpToSection(GlobalKey key) {
    final ctx = key.currentContext;
    if (ctx == null) return;
    Scrollable.ensureVisible(
      ctx,
      alignment: 0.05,
      duration: const Duration(milliseconds: 300),
      curve: Curves.easeOut,
    );
  }

  Widget _buildStickyBottomBar() {
    return Container(
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border(top: BorderSide(color: AppColors.border)),
      ),
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              PrimaryButton(
                label: widget.isEditMode ? 'Update Lead' : 'Create Lead',
                onPressed: _isLoading
                    ? null
                    : () {
                        _saveAndAddAnother = false;
                        _handleSubmit();
                      },
                isLoading: _isLoading && !_saveAndAddAnother,
              ),
              if (!widget.isEditMode) ...[
                const SizedBox(height: 8),
                TextButton.icon(
                  onPressed: _isLoading
                      ? null
                      : () {
                          _saveAndAddAnother = true;
                          _handleSubmit();
                        },
                  icon: _isLoading && _saveAndAddAnother
                      ? const SizedBox(
                          width: 14,
                          height: 14,
                          child: CircularProgressIndicator(strokeWidth: 1.5),
                        )
                      : Icon(LucideIcons.plus, size: 16),
                  label: const Text('Save & Add Another'),
                  style: TextButton.styleFrom(
                    foregroundColor: AppColors.primary600,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildSectionTitle(String title) {
    return Text(
      title.toUpperCase(),
      style: AppTypography.overline.copyWith(
        color: AppColors.textSecondary,
        letterSpacing: 1.2,
      ),
    );
  }

  // ---------------------------------------------------------------------
  // Section: Identity
  // ---------------------------------------------------------------------

  Widget _buildIdentitySection() {
    return Column(
      key: _identitySectionKey,
      children: [
        FloatingLabelInput(
          key: _titleKey,
          label: 'Lead Title *',
          hint: 'e.g. Website Inquiry, Q4 Demo',
          controller: _titleController,
          prefixIcon: LucideIcons.tag,
          maxLength: 200,
          textInputAction: TextInputAction.next,
          validator: (v) =>
              (v == null || v.trim().isEmpty) ? 'Lead title is required' : null,
        ),
        const SizedBox(height: 16),
        // Salutation + First name on one row mirrors the web layout.
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
              width: 110,
              child: _PickerField(
                label: 'Salutation',
                value: _salutation ?? '—',
                onTap: _isLoading ? null : _showSalutationPicker,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: FloatingLabelInput(
                key: _firstNameKey,
                label: 'First Name *',
                hint: 'John',
                controller: _firstNameController,
                prefixIcon: LucideIcons.user,
                maxLength: 50,
                textCapitalization: TextCapitalization.words,
                textInputAction: TextInputAction.next,
                validator: (v) => (v == null || v.trim().isEmpty)
                    ? 'First name is required'
                    : null,
              ),
            ),
          ],
        ),
        const SizedBox(height: 16),
        FloatingLabelInput(
          key: _lastNameKey,
          label: 'Last Name *',
          hint: 'Doe',
          controller: _lastNameController,
          prefixIcon: LucideIcons.user,
          maxLength: 50,
          textCapitalization: TextCapitalization.words,
          textInputAction: TextInputAction.next,
          validator: (v) =>
              (v == null || v.trim().isEmpty) ? 'Last name is required' : null,
        ),
      ],
    );
  }

  // ---------------------------------------------------------------------
  // Section: Contact
  // ---------------------------------------------------------------------

  Widget _buildContactSection() {
    return Column(
      key: _contactSectionKey,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        FloatingLabelInput(
          key: _emailKey,
          label: 'Email *',
          hint: 'john@acme.com',
          controller: _emailController,
          focusNode: _emailFocusNode,
          prefixIcon: LucideIcons.mail,
          keyboardType: TextInputType.emailAddress,
          maxLength: 254,
          textInputAction: TextInputAction.next,
          validator: (value) {
            final v = (value ?? '').trim();
            if (v.isEmpty) return 'Email is required';
            if (!_emailRegex.hasMatch(v)) return 'Enter a valid email';
            return null;
          },
        ),
        if (_duplicateLeadId != null || _checkingDuplicate)
          _buildDuplicateEmailHint(),
        const SizedBox(height: 16),
        FloatingLabelInput(
          key: _phoneKey,
          label: 'Phone (Optional)',
          hint: '+1 (555) 123-4567',
          controller: _phoneController,
          prefixIcon: LucideIcons.phone,
          keyboardType: TextInputType.phone,
          maxLength: 20,
          textInputAction: TextInputAction.next,
          inputFormatters: [FilteringTextInputFormatter.allow(_phoneAllowed)],
          onSubmitted: (_) => _normalizePhoneInPlace(),
          validator: (value) {
            final v = (value ?? '').trim();
            if (v.isEmpty) return null;
            // Backend's flexible_phone_validator wants at least a few digits.
            final digits = v.replaceAll(RegExp(r'\D'), '');
            if (digits.length < 6) return 'Enter a valid phone';
            return null;
          },
        ),
        const SizedBox(height: 16),
        FloatingLabelInput(
          key: _linkedinKey,
          label: 'LinkedIn (Optional)',
          hint: 'https://linkedin.com/in/...',
          controller: _linkedinController,
          prefixIcon: LucideIcons.link,
          keyboardType: TextInputType.url,
          maxLength: 255,
          textInputAction: TextInputAction.next,
          validator: _urlValidator,
        ),
      ],
    );
  }

  Widget _buildDuplicateEmailHint() {
    if (_checkingDuplicate) {
      return Padding(
        padding: const EdgeInsets.only(top: 8, left: 4),
        child: Row(
          children: [
            const SizedBox(
              width: 12,
              height: 12,
              child: CircularProgressIndicator(strokeWidth: 1.5),
            ),
            const SizedBox(width: 8),
            Text(
              'Checking for duplicates…',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          ],
        ),
      );
    }
    return Padding(
      padding: const EdgeInsets.only(top: 8, left: 4),
      child: InkWell(
        onTap: () {
          if (_duplicateLeadId == null) return;
          context.push('/leads/${_duplicateLeadId!}');
        },
        child: Row(
          children: [
            Icon(
              LucideIcons.alertTriangle,
              size: 16,
              color: AppColors.warning600,
            ),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                'A lead with this email already exists'
                '${_duplicateLeadLabel != null ? ', ${_duplicateLeadLabel!}' : ''}.'
                ' Tap to open.',
                style: AppTypography.caption.copyWith(
                  color: AppColors.warning700,
                  decoration: TextDecoration.underline,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  // Reformats the phone field on blur/submit: collapses multiple spaces, trims,
  // and removes stray separators around digits. Intentionally light-touch.
  // We don't enforce a region format because the backend accepts anything
  // with >=6 digits.
  void _normalizePhoneInPlace() {
    final raw = _phoneController.text;
    if (raw.isEmpty) return;
    var trimmed = raw.trim().replaceAll(RegExp(r'\s+'), ' ');
    // Strip trailing/leading separators that aren't digits/+.
    trimmed = trimmed.replaceAll(RegExp(r'^[^\d+]+|[^\d)]+$'), '');
    if (trimmed != raw) {
      _phoneController.text = trimmed;
      _phoneController.selection = TextSelection.collapsed(
        offset: trimmed.length,
      );
    }
  }

  void _onEmailFocusChange() {
    if (_emailFocusNode.hasFocus) return;
    // Blur, kick off a duplicate check on create mode only.
    if (widget.isEditMode) return;
    final email = _emailController.text.trim();
    if (email.isEmpty || !_emailRegex.hasMatch(email)) {
      if (_duplicateLeadId != null || _checkingDuplicate) {
        setState(() {
          _duplicateLeadId = null;
          _duplicateLeadLabel = null;
          _checkingDuplicate = false;
        });
      }
      return;
    }
    if (email == _lastCheckedEmail) return;
    _lastCheckedEmail = email;
    _runDuplicateEmailCheck(email);
  }

  Future<void> _runDuplicateEmailCheck(String email) async {
    setState(() {
      _checkingDuplicate = true;
      _duplicateLeadId = null;
      _duplicateLeadLabel = null;
    });
    final match = await ref.read(leadsProvider.notifier).findLeadByEmail(email);
    if (!mounted) return;
    setState(() {
      _checkingDuplicate = false;
      _duplicateLeadId = match?.id;
      _duplicateLeadLabel = match?.label;
    });
  }

  String? _urlValidator(String? value) {
    final v = (value ?? '').trim();
    if (v.isEmpty) return null;
    final uri = Uri.tryParse(v);
    if (uri == null ||
        !(uri.hasScheme && uri.hasAuthority) ||
        (uri.scheme != 'http' && uri.scheme != 'https')) {
      return 'Enter a valid URL (https://...)';
    }
    return null;
  }

  // ---------------------------------------------------------------------
  // Section: Company
  // ---------------------------------------------------------------------

  Widget _buildCompanySection() {
    return Column(
      key: _companySectionKey,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        FloatingLabelInput(
          key: _companyKey,
          label: 'Company *',
          hint: 'Acme Inc.',
          controller: _companyController,
          prefixIcon: LucideIcons.building2,
          maxLength: 255,
          textCapitalization: TextCapitalization.words,
          textInputAction: TextInputAction.next,
          validator: (value) {
            if (value == null || value.trim().isEmpty) {
              return 'Company is required';
            }
            return null;
          },
        ),
        const SizedBox(height: 16),
        FloatingLabelInput(
          label: 'Job Title (Optional)',
          hint: 'e.g. Marketing Manager',
          controller: _jobTitleController,
          prefixIcon: LucideIcons.briefcase,
          maxLength: 100,
          textInputAction: TextInputAction.next,
        ),
        const SizedBox(height: 16),
        FloatingLabelInput(
          key: _websiteKey,
          label: 'Website (Optional)',
          hint: 'https://acme.com',
          controller: _websiteController,
          prefixIcon: LucideIcons.globe,
          keyboardType: TextInputType.url,
          maxLength: 255,
          textInputAction: TextInputAction.next,
          validator: _urlValidator,
        ),
        const SizedBox(height: 16),
        // Industry picker, backed by backend's INDCHOICES so values match
        // across web + mobile and aren't fragmented by free-text drift.
        Padding(
          key: _industryKey,
          padding: EdgeInsets.zero,
          child: _PickerField(
            label: 'Industry (Optional)',
            value: _industryController.text.isEmpty
                ? 'Select industry'
                : _industryController.text,
            leading: Icon(
              LucideIcons.factory,
              size: 20,
              color: AppColors.textSecondary,
            ),
            onTap: _isLoading ? null : _showIndustryPicker,
          ),
        ),
      ],
    );
  }

  // ---------------------------------------------------------------------
  // Section: Pipeline & Deal
  // ---------------------------------------------------------------------

  Widget _buildPipelineSection() {
    return Column(
      key: _pipelineSectionKey,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _PickerField(
          label: 'Status',
          value: _status.displayName,
          leading: Icon(_statusIcon(_status), size: 20, color: _status.color),
          onTap: _isLoading ? null : _showStatusPicker,
        ),
        const SizedBox(height: 16),
        _PickerField(
          label: 'Source',
          value: _source?.displayName ?? 'None',
          leading: Icon(
            _source?.icon ?? LucideIcons.helpCircle,
            size: 20,
            color: AppColors.textSecondary,
          ),
          onTap: _isLoading ? null : _showSourcePicker,
        ),
        const SizedBox(height: 16),
        Text(
          'Rating',
          style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(height: 8),
        _buildRatingSelector(),
        const SizedBox(height: 16),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              flex: 3,
              child: FloatingLabelInput(
                key: _opportunityAmountKey,
                label: 'Estimated Value',
                hint: '0.00',
                controller: _opportunityAmountController,
                prefixIcon: LucideIcons.dollarSign,
                keyboardType: const TextInputType.numberWithOptions(
                  decimal: true,
                ),
                inputFormatters: [
                  FilteringTextInputFormatter.allow(RegExp(r'[0-9.]')),
                ],
                textInputAction: TextInputAction.next,
                validator: (value) {
                  final v = (value ?? '').trim();
                  if (v.isEmpty) return null;
                  final parsed = double.tryParse(v);
                  if (parsed == null) return 'Enter a number';
                  if (parsed < 0) return 'Must be ≥ 0';
                  return null;
                },
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              flex: 2,
              child: _PickerField(
                label: 'Currency',
                value: _currency ?? 'Select',
                onTap: _isLoading ? null : _showCurrencyPicker,
              ),
            ),
          ],
        ),
        const SizedBox(height: 16),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: FloatingLabelInput(
                key: _probabilityKey,
                label: 'Probability %',
                hint: '0-100',
                controller: _probabilityController,
                prefixIcon: LucideIcons.percent,
                keyboardType: TextInputType.number,
                inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                maxLength: 3,
                textInputAction: TextInputAction.next,
                validator: (value) {
                  final v = (value ?? '').trim();
                  if (v.isEmpty) return null;
                  final parsed = int.tryParse(v);
                  if (parsed == null) return 'Enter a number';
                  if (parsed < 0 || parsed > 100) return '0-100';
                  return null;
                },
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: _DateField(
                label: 'Close Date',
                value: _closeDate,
                onTap: _isLoading
                    ? null
                    : () => _pickDate(
                        initial: _closeDate,
                        onSelect: (d) => setState(() => _closeDate = d),
                      ),
                onClear: _closeDate == null || _isLoading
                    ? null
                    : () => setState(() => _closeDate = null),
              ),
            ),
          ],
        ),
      ],
    );
  }

  // ---------------------------------------------------------------------
  // Section: Address
  // ---------------------------------------------------------------------

  Widget _buildAddressSection() {
    return Column(
      key: _addressSectionKey,
      children: [
        FloatingLabelInput(
          label: 'Street (Optional)',
          hint: '123 Market St',
          controller: _addressLineController,
          prefixIcon: LucideIcons.mapPin,
          maxLength: 255,
          textInputAction: TextInputAction.next,
        ),
        const SizedBox(height: 16),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: FloatingLabelInput(
                label: 'City',
                hint: 'San Francisco',
                controller: _cityController,
                prefixIcon: LucideIcons.building,
                maxLength: 100,
                textCapitalization: TextCapitalization.words,
                textInputAction: TextInputAction.next,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: FloatingLabelInput(
                label: 'State',
                hint: 'CA',
                controller: _stateController,
                maxLength: 100,
                textCapitalization: TextCapitalization.words,
                textInputAction: TextInputAction.next,
              ),
            ),
          ],
        ),
        const SizedBox(height: 16),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: FloatingLabelInput(
                label: 'Postcode',
                hint: '94105',
                controller: _postcodeController,
                maxLength: 20,
                textInputAction: TextInputAction.next,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: _PickerField(
                label: 'Country',
                value: _countryLabel(_countryController.text) ?? 'Select',
                leading: Icon(
                  LucideIcons.globe2,
                  size: 20,
                  color: AppColors.textSecondary,
                ),
                onTap: _isLoading ? null : _showCountryPicker,
              ),
            ),
          ],
        ),
      ],
    );
  }

  // ---------------------------------------------------------------------
  // Section: Activity Dates
  // ---------------------------------------------------------------------

  Widget _buildActivitySection() {
    return Row(
      key: _activitySectionKey,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: _DateField(
            label: 'Last Contacted',
            value: _lastContacted,
            onTap: _isLoading
                ? null
                : () => _pickDate(
                    initial: _lastContacted,
                    onSelect: (d) => setState(() => _lastContacted = d),
                    lastDate: DateTime.now(),
                  ),
            onClear: _lastContacted == null || _isLoading
                ? null
                : () => setState(() => _lastContacted = null),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: _DateField(
            label: 'Next Follow-up',
            value: _nextFollowUp,
            onTap: _isLoading
                ? null
                : () => _pickDate(
                    initial: _nextFollowUp,
                    onSelect: (d) => setState(() => _nextFollowUp = d),
                  ),
            onClear: _nextFollowUp == null || _isLoading
                ? null
                : () => setState(() => _nextFollowUp = null),
          ),
        ),
      ],
    );
  }

  // ---------------------------------------------------------------------
  // Section: Relationships
  // ---------------------------------------------------------------------

  Widget _buildRelationshipsSection() {
    final users = ref.watch(usersProvider);
    final tags = ref.watch(tagsProvider);

    return Column(
      key: _relationshipsSectionKey,
      children: [
        _buildMultiSelectField(
          label: 'Assigned To',
          icon: LucideIcons.users,
          selectedCount: _assignedToIds.length,
          selectedLabel: _selectedUsersLabel(users),
          onTap: _isLoading ? null : _showAssignedToPicker,
        ),
        const SizedBox(height: 16),
        _buildMultiSelectField(
          label: 'Tags',
          icon: LucideIcons.tag,
          selectedCount: _tagIds.length,
          selectedLabel: _selectedTagsLabel(tags),
          onTap: _isLoading ? null : _showTagsPicker,
        ),
      ],
    );
  }

  String _selectedUsersLabel(List<UserLookup> users) {
    if (_assignedToIds.isEmpty) return 'Select users';
    final selected = users.where((u) => _assignedToIds.contains(u.id)).toList();
    if (selected.isEmpty) return '${_assignedToIds.length} selected';
    if (selected.length == 1) return selected.first.displayName;
    return '${selected.length} users selected';
  }

  String _selectedTagsLabel(List<TagLookup> tags) {
    if (_tagIds.isEmpty) return 'Select tags';
    final selected = tags.where((t) => _tagIds.contains(t.id)).toList();
    if (selected.isEmpty) return '${_tagIds.length} selected';
    if (selected.length == 1) return selected.first.name;
    return '${selected.length} tags selected';
  }

  // ---------------------------------------------------------------------
  // Section: Custom Fields
  // ---------------------------------------------------------------------

  Widget _buildCustomFieldsSection() {
    final defs = [..._customFieldDefs]
      ..sort((a, b) => a.displayOrder.compareTo(b.displayOrder));
    return Column(
      key: _customFieldsSectionKey,
      children: [
        for (final def in defs) ...[
          _buildCustomFieldInput(def),
          const SizedBox(height: 16),
        ],
      ],
    );
  }

  Widget _buildCustomFieldInput(CustomFieldDefinition def) {
    final label = def.isRequired ? '${def.label} *' : def.label;
    switch (def.fieldType) {
      case CustomFieldType.text:
      case CustomFieldType.number:
        return FloatingLabelInput(
          label: label,
          controller: _customFieldControllers.putIfAbsent(
            def.key,
            () => TextEditingController(
              text: _customFieldValues[def.key]?.toString() ?? '',
            ),
          ),
          maxLength: 500,
          keyboardType: def.fieldType == CustomFieldType.number
              ? const TextInputType.numberWithOptions(decimal: true)
              : null,
          inputFormatters: def.fieldType == CustomFieldType.number
              ? [FilteringTextInputFormatter.allow(RegExp(r'[0-9.\-]'))]
              : null,
          onChanged: (v) {
            _customFieldValues[def.key] =
                def.fieldType == CustomFieldType.number
                ? (v.trim().isEmpty ? null : v.trim())
                : v;
          },
        );
      case CustomFieldType.textarea:
        return TextAreaField(
          label: label,
          controller: _customFieldControllers.putIfAbsent(
            def.key,
            () => TextEditingController(
              text: _customFieldValues[def.key]?.toString() ?? '',
            ),
          ),
          maxLines: 4,
          onChanged: (v) => _customFieldValues[def.key] = v,
        );
      case CustomFieldType.date:
        final current = _customFieldValues[def.key];
        DateTime? dt;
        if (current is String && current.isNotEmpty) {
          dt = DateTime.tryParse(current);
        }
        return _DateField(
          label: label,
          value: dt,
          onTap: _isLoading
              ? null
              : () => _pickDate(
                  initial: dt,
                  onSelect: (d) => setState(() {
                    _customFieldValues[def.key] = _formatDate(d);
                  }),
                ),
          onClear: dt == null || _isLoading
              ? null
              : () => setState(() => _customFieldValues[def.key] = null),
        );
      case CustomFieldType.dropdown:
        final current = _customFieldValues[def.key]?.toString();
        final selected = def.options.firstWhere(
          (o) => o.value == current,
          orElse: () => const CustomFieldOption(value: '', label: ''),
        );
        return _PickerField(
          label: label,
          value: selected.value.isEmpty ? 'Select' : selected.label,
          onTap: _isLoading ? null : () => _showCustomDropdownPicker(def),
        );
      case CustomFieldType.checkbox:
        final value = _customFieldValues[def.key] == true;
        return Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
          decoration: BoxDecoration(
            color: AppColors.gray50,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: AppColors.border),
          ),
          child: Row(
            children: [
              Expanded(child: Text(label, style: AppTypography.body)),
              Switch(
                value: value,
                onChanged: _isLoading
                    ? null
                    : (v) => setState(() => _customFieldValues[def.key] = v),
              ),
            ],
          ),
        );
    }
  }

  void _showCustomDropdownPicker(CustomFieldDefinition def) {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _PickerBottomSheet(
        title: def.label,
        options: [
          _PickerOption(
            label: 'None',
            isSelected: (_customFieldValues[def.key]?.toString() ?? '').isEmpty,
            onTap: () {
              setState(() => _customFieldValues[def.key] = null);
              Navigator.pop(context);
            },
          ),
          for (final opt in def.options)
            _PickerOption(
              label: opt.label,
              isSelected: _customFieldValues[def.key]?.toString() == opt.value,
              onTap: () {
                setState(() => _customFieldValues[def.key] = opt.value);
                Navigator.pop(context);
              },
            ),
        ],
      ),
    );
  }

  // ---------------------------------------------------------------------
  // Shared field widgets
  // ---------------------------------------------------------------------

  Widget _buildMultiSelectField({
    required String label,
    required IconData icon,
    required int selectedCount,
    required String selectedLabel,
    required VoidCallback? onTap,
  }) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(height: 8),
        GestureDetector(
          onTap: onTap,
          child: Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
            decoration: BoxDecoration(
              color: AppColors.gray50,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: AppColors.border),
            ),
            child: Row(
              children: [
                Icon(icon, size: 20, color: AppColors.textSecondary),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    selectedLabel,
                    style: AppTypography.body.copyWith(
                      color: selectedCount > 0
                          ? AppColors.textPrimary
                          : AppColors.textSecondary,
                    ),
                  ),
                ),
                if (selectedCount > 0) ...[
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 2,
                    ),
                    decoration: BoxDecoration(
                      color: AppColors.primary100,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Text(
                      selectedCount.toString(),
                      style: AppTypography.caption.copyWith(
                        color: AppColors.primary600,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                ],
                Icon(
                  LucideIcons.chevronDown,
                  size: 20,
                  color: AppColors.textSecondary,
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildRatingSelector() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        Container(
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: AppColors.border),
          ),
          child: Row(
            children: LeadRating.values.map((rating) {
              final isSelected = _rating == rating;
              final isFirst = rating == LeadRating.values.first;
              final isLast = rating == LeadRating.values.last;

              return Expanded(
                child: GestureDetector(
                  onTap: _isLoading
                      ? null
                      : () => setState(() => _rating = rating),
                  child: AnimatedContainer(
                    duration: AppDurations.fast,
                    padding: const EdgeInsets.symmetric(vertical: 16),
                    decoration: BoxDecoration(
                      color: isSelected ? rating.color : AppColors.surface,
                      borderRadius: BorderRadius.horizontal(
                        left: isFirst ? const Radius.circular(11) : Radius.zero,
                        right: isLast ? const Radius.circular(11) : Radius.zero,
                      ),
                    ),
                    child: Text(
                      rating.displayName,
                      textAlign: TextAlign.center,
                      style: AppTypography.label.copyWith(
                        color: isSelected
                            ? Colors.white
                            : AppColors.textSecondary,
                        fontWeight: isSelected
                            ? FontWeight.w600
                            : FontWeight.w500,
                      ),
                    ),
                  ),
                ),
              );
            }).toList(),
          ),
        ),
        if (_rating != null)
          TextButton(
            onPressed: _isLoading ? null : () => setState(() => _rating = null),
            style: TextButton.styleFrom(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              minimumSize: const Size(0, 28),
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            child: Text(
              'Clear rating',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          ),
      ],
    );
  }

  // ---------------------------------------------------------------------
  // Pickers
  // ---------------------------------------------------------------------

  void _showSalutationPicker() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _PickerBottomSheet(
        title: 'Salutation',
        options: [
          _PickerOption(
            label: 'None',
            isSelected: _salutation == null,
            onTap: () {
              setState(() => _salutation = null);
              Navigator.pop(context);
            },
          ),
          for (final sal in _kSalutations)
            _PickerOption(
              label: sal,
              isSelected: _salutation == sal,
              onTap: () {
                setState(() => _salutation = sal);
                Navigator.pop(context);
              },
            ),
        ],
      ),
    );
  }

  void _showStatusPicker() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _PickerBottomSheet(
        title: 'Select Status',
        options: LeadStatus.values
            .map(
              (status) => _PickerOption(
                label: status.displayName,
                isSelected: _status == status,
                color: status.color,
                icon: _statusIcon(status),
                onTap: () {
                  setState(() {
                    _status = status;
                    // Pre-fill probability with the status-appropriate default
                    // when the user hasn't typed one yet. We don't overwrite a
                    // value the user already entered.
                    if (_probabilityController.text.trim().isEmpty) {
                      _probabilityController.text =
                          _suggestedProbabilityForStatus(status).toString();
                    }
                  });
                  Navigator.pop(context);
                },
              ),
            )
            .toList(),
      ),
    );
  }

  void _showSourcePicker() {
    final sources = LeadSource.values
        .where((s) => s != LeadSource.none)
        .toList();
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _PickerBottomSheet(
        title: 'Select Source',
        options: [
          _PickerOption(
            label: 'None',
            isSelected: _source == null,
            onTap: () {
              setState(() => _source = null);
              Navigator.pop(context);
            },
          ),
          for (final source in sources)
            _PickerOption(
              label: source.displayName,
              isSelected: _source == source,
              icon: source.icon,
              onTap: () {
                setState(() => _source = source);
                Navigator.pop(context);
              },
            ),
        ],
      ),
    );
  }

  void _showIndustryPicker() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _SearchablePickerSheet(
        title: 'Industry',
        options: [
          (value: '', label: 'None'),
          for (final ind in _kIndustries) (value: ind, label: ind),
        ],
        selectedValue: _industryController.text,
        onSelected: (value) {
          setState(() => _industryController.text = value);
        },
      ),
    );
  }

  void _showCountryPicker() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _SearchablePickerSheet(
        title: 'Country',
        options: [
          (value: '', label: 'None'),
          for (final c in _kCountries) (value: c.code, label: c.label),
        ],
        selectedValue: _countryController.text,
        onSelected: (value) {
          setState(() => _countryController.text = value);
        },
      ),
    );
  }

  void _showCurrencyPicker() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _PickerBottomSheet(
        title: 'Select Currency',
        options: [
          _PickerOption(
            label: 'None',
            isSelected: _currency == null || _currency!.isEmpty,
            onTap: () {
              setState(() => _currency = null);
              Navigator.pop(context);
            },
          ),
          for (final c in _kCurrencies)
            _PickerOption(
              label: c.label,
              isSelected: _currency == c.code,
              onTap: () {
                setState(() => _currency = c.code);
                Navigator.pop(context);
              },
            ),
        ],
      ),
    );
  }

  void _showAssignedToPicker() {
    final users = ref.read(usersProvider);

    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _MultiSelectPickerSheet<UserLookup>(
        title: 'Assign To',
        items: users,
        selectedIds: _assignedToIds,
        getItemId: (user) => user.id,
        getItemLabel: (user) => user.displayName,
        getItemSubtitle: (user) => user.email,
        onSelectionChanged: (ids) {
          setState(() => _assignedToIds = ids);
        },
      ),
    );
  }

  void _showTagsPicker() {
    final tags = ref.read(tagsProvider);

    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _MultiSelectPickerSheet<TagLookup>(
        title: 'Select Tags',
        items: tags,
        selectedIds: _tagIds,
        getItemId: (tag) => tag.id,
        getItemLabel: (tag) => tag.name,
        getItemColor: (tag) => _tagColor(tag.color),
        onSelectionChanged: (ids) {
          setState(() => _tagIds = ids);
        },
      ),
    );
  }

  Color _tagColor(String colorName) {
    switch (colorName.toLowerCase()) {
      case 'red':
        return AppColors.danger500;
      case 'orange':
        return AppColors.warning500;
      case 'yellow':
        return Colors.amber;
      case 'green':
        return AppColors.success500;
      case 'blue':
        return AppColors.primary500;
      case 'purple':
        return AppColors.purple500;
      case 'pink':
        return Colors.pink;
      default:
        return AppColors.gray500;
    }
  }

  Future<void> _pickDate({
    required DateTime? initial,
    required ValueChanged<DateTime> onSelect,
    DateTime? firstDate,
    DateTime? lastDate,
  }) async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: initial ?? now,
      firstDate: firstDate ?? DateTime(now.year - 10),
      lastDate: lastDate ?? DateTime(now.year + 10),
    );
    if (picked != null) onSelect(picked);
  }
}

// ---------------------------------------------------------------------
// Reusable inline widgets
// ---------------------------------------------------------------------

class _PickerField extends StatelessWidget {
  final String label;
  final String value;
  final Widget? leading;
  final VoidCallback? onTap;

  const _PickerField({
    required this.label,
    required this.value,
    this.leading,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(height: 8),
        GestureDetector(
          onTap: onTap,
          child: Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
            decoration: BoxDecoration(
              color: AppColors.gray50,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: AppColors.border),
            ),
            child: Row(
              children: [
                if (leading != null) ...[leading!, const SizedBox(width: 12)],
                Expanded(
                  child: Text(
                    value,
                    style: AppTypography.body,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                Icon(
                  LucideIcons.chevronDown,
                  size: 20,
                  color: AppColors.textSecondary,
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _DateField extends StatelessWidget {
  final String label;
  final DateTime? value;
  final VoidCallback? onTap;
  final VoidCallback? onClear;

  const _DateField({
    required this.label,
    required this.value,
    this.onTap,
    this.onClear,
  });

  @override
  Widget build(BuildContext context) {
    final formatted = value == null
        ? 'Select date'
        : DateFormat('MMM d, yyyy').format(value!);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(height: 8),
        GestureDetector(
          onTap: onTap,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
            decoration: BoxDecoration(
              color: AppColors.gray50,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: AppColors.border),
            ),
            child: Row(
              children: [
                Icon(
                  LucideIcons.calendar,
                  size: 20,
                  color: AppColors.textSecondary,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    formatted,
                    style: AppTypography.body.copyWith(
                      color: value == null
                          ? AppColors.textSecondary
                          : AppColors.textPrimary,
                    ),
                  ),
                ),
                if (onClear != null)
                  GestureDetector(
                    onTap: onClear,
                    child: Icon(
                      LucideIcons.x,
                      size: 18,
                      color: AppColors.textSecondary,
                    ),
                  ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

/// Single-select picker with a search box. Used for the long lists where the
/// plain [_PickerBottomSheet] would become a scroll-and-hunt exercise
/// (industries: ~30 entries, countries: ~240 entries).
class _SearchablePickerSheet extends StatefulWidget {
  final String title;
  final List<({String value, String label})> options;
  final String selectedValue;
  final ValueChanged<String> onSelected;

  const _SearchablePickerSheet({
    required this.title,
    required this.options,
    required this.selectedValue,
    required this.onSelected,
  });

  @override
  State<_SearchablePickerSheet> createState() => _SearchablePickerSheetState();
}

class _SearchablePickerSheetState extends State<_SearchablePickerSheet> {
  String _query = '';

  @override
  Widget build(BuildContext context) {
    final q = _query.trim().toLowerCase();
    final filtered = q.isEmpty
        ? widget.options
        : widget.options
              .where((o) => o.label.toLowerCase().contains(q))
              .toList();

    return DraggableScrollableSheet(
      initialChildSize: 0.7,
      minChildSize: 0.5,
      maxChildSize: 0.9,
      expand: false,
      builder: (context, scrollController) => Column(
        children: [
          Container(
            margin: const EdgeInsets.only(top: 12),
            width: 40,
            height: 4,
            decoration: BoxDecoration(
              color: AppColors.gray300,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(16),
            child: Text(widget.title, style: AppTypography.h3),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: TextField(
              decoration: InputDecoration(
                hintText: 'Search...',
                prefixIcon: const Icon(LucideIcons.search, size: 20),
                filled: true,
                fillColor: AppColors.gray50,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                  borderSide: BorderSide.none,
                ),
                contentPadding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 12,
                ),
              ),
              onChanged: (value) => setState(() => _query = value),
            ),
          ),
          const SizedBox(height: 8),
          Expanded(
            child: ListView.builder(
              controller: scrollController,
              itemCount: filtered.length,
              itemBuilder: (context, index) {
                final opt = filtered[index];
                final isSelected = opt.value == widget.selectedValue;
                return _PickerOption(
                  label: opt.label,
                  isSelected: isSelected,
                  onTap: () {
                    widget.onSelected(opt.value);
                    Navigator.pop(context);
                  },
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

/// Picker bottom sheet
class _PickerBottomSheet extends StatelessWidget {
  final String title;
  final List<_PickerOption> options;

  const _PickerBottomSheet({required this.title, required this.options});

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            margin: const EdgeInsets.only(top: 12),
            width: 40,
            height: 4,
            decoration: BoxDecoration(
              color: AppColors.gray300,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(16),
            child: Text(title, style: AppTypography.h3),
          ),
          Flexible(
            child: SingleChildScrollView(
              child: Column(mainAxisSize: MainAxisSize.min, children: options),
            ),
          ),
          const SizedBox(height: 16),
        ],
      ),
    );
  }
}

/// Picker option item
class _PickerOption extends StatelessWidget {
  final String label;
  final bool isSelected;
  final Color? color;
  final IconData? icon;
  final VoidCallback onTap;

  const _PickerOption({
    required this.label,
    required this.isSelected,
    this.color,
    this.icon,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        child: Row(
          children: [
            if (color != null) ...[
              Container(
                width: 12,
                height: 12,
                decoration: BoxDecoration(color: color, shape: BoxShape.circle),
              ),
              const SizedBox(width: 12),
            ],
            if (icon != null) ...[
              Icon(icon, size: 20, color: AppColors.textSecondary),
              const SizedBox(width: 12),
            ],
            Expanded(
              child: Text(
                label,
                style: AppTypography.body.copyWith(
                  fontWeight: isSelected ? FontWeight.w600 : FontWeight.normal,
                  color: isSelected
                      ? AppColors.primary600
                      : AppColors.textPrimary,
                ),
              ),
            ),
            if (isSelected)
              Icon(LucideIcons.check, size: 20, color: AppColors.primary600),
          ],
        ),
      ),
    );
  }
}

/// Multi-select picker sheet
class _MultiSelectPickerSheet<T> extends StatefulWidget {
  final String title;
  final List<T> items;
  final List<String> selectedIds;
  final String Function(T) getItemId;
  final String Function(T) getItemLabel;
  final String? Function(T)? getItemSubtitle;
  final Color? Function(T)? getItemColor;
  final void Function(List<String>) onSelectionChanged;

  const _MultiSelectPickerSheet({
    required this.title,
    required this.items,
    required this.selectedIds,
    required this.getItemId,
    required this.getItemLabel,
    this.getItemSubtitle,
    this.getItemColor,
    required this.onSelectionChanged,
  });

  @override
  State<_MultiSelectPickerSheet<T>> createState() =>
      _MultiSelectPickerSheetState<T>();
}

class _MultiSelectPickerSheetState<T>
    extends State<_MultiSelectPickerSheet<T>> {
  late List<String> _selectedIds;
  String _searchQuery = '';

  @override
  void initState() {
    super.initState();
    _selectedIds = List.from(widget.selectedIds);
  }

  List<T> get _filteredItems {
    if (_searchQuery.isEmpty) return widget.items;
    return widget.items.where((item) {
      final label = widget.getItemLabel(item).toLowerCase();
      final subtitle = widget.getItemSubtitle?.call(item)?.toLowerCase() ?? '';
      return label.contains(_searchQuery.toLowerCase()) ||
          subtitle.contains(_searchQuery.toLowerCase());
    }).toList();
  }

  @override
  Widget build(BuildContext context) {
    return DraggableScrollableSheet(
      initialChildSize: 0.7,
      minChildSize: 0.5,
      maxChildSize: 0.9,
      expand: false,
      builder: (context, scrollController) => Column(
        children: [
          Container(
            margin: const EdgeInsets.only(top: 12),
            width: 40,
            height: 4,
            decoration: BoxDecoration(
              color: AppColors.gray300,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(16),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(widget.title, style: AppTypography.h3),
                TextButton(
                  // Themed buttons carry an infinite minimum width, which a Row
                  // does not bound. Without this the row fails to lay out and the
                  // screen paints nothing. See AppLayout.buttonMinSizeInRow.
                  style: TextButton.styleFrom(
                    minimumSize: AppLayout.buttonMinSizeInRow,
                  ),
                  onPressed: () {
                    widget.onSelectionChanged(_selectedIds);
                    Navigator.pop(context);
                  },
                  child: const Text('Done'),
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: TextField(
              decoration: InputDecoration(
                hintText: 'Search...',
                prefixIcon: const Icon(LucideIcons.search, size: 20),
                filled: true,
                fillColor: AppColors.gray50,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                  borderSide: BorderSide.none,
                ),
                contentPadding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 12,
                ),
              ),
              onChanged: (value) => setState(() => _searchQuery = value),
            ),
          ),
          const SizedBox(height: 8),
          Expanded(
            child: ListView.builder(
              controller: scrollController,
              itemCount: _filteredItems.length,
              itemBuilder: (context, index) {
                final item = _filteredItems[index];
                final id = widget.getItemId(item);
                final isSelected = _selectedIds.contains(id);
                final color = widget.getItemColor?.call(item);
                final subtitle = widget.getItemSubtitle?.call(item);

                return InkWell(
                  onTap: () {
                    setState(() {
                      if (isSelected) {
                        _selectedIds.remove(id);
                      } else {
                        _selectedIds.add(id);
                      }
                    });
                  },
                  child: Padding(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 16,
                      vertical: 12,
                    ),
                    child: Row(
                      children: [
                        Container(
                          width: 24,
                          height: 24,
                          decoration: BoxDecoration(
                            color: isSelected
                                ? AppColors.primary600
                                : AppColors.surface,
                            borderRadius: BorderRadius.circular(6),
                            border: Border.all(
                              color: isSelected
                                  ? AppColors.primary600
                                  : AppColors.gray300,
                              width: 2,
                            ),
                          ),
                          child: isSelected
                              ? const Icon(
                                  LucideIcons.check,
                                  size: 16,
                                  color: Colors.white,
                                )
                              : null,
                        ),
                        const SizedBox(width: 12),
                        if (color != null) ...[
                          Container(
                            width: 12,
                            height: 12,
                            decoration: BoxDecoration(
                              color: color,
                              shape: BoxShape.circle,
                            ),
                          ),
                          const SizedBox(width: 12),
                        ],
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                widget.getItemLabel(item),
                                style: AppTypography.body.copyWith(
                                  fontWeight: isSelected
                                      ? FontWeight.w600
                                      : FontWeight.normal,
                                ),
                              ),
                              if (subtitle != null && subtitle.isNotEmpty)
                                Text(
                                  subtitle,
                                  style: AppTypography.caption.copyWith(
                                    color: AppColors.textSecondary,
                                  ),
                                ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}
