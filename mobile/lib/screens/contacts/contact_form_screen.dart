import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/contact.dart';
import '../../data/models/lookup_models.dart';
import '../../providers/contacts_provider.dart';
import '../../providers/lookup_provider.dart';
import '../../widgets/forms/unsaved_changes.dart';

/// Create or edit a contact.
class ContactFormScreen extends ConsumerStatefulWidget {
  const ContactFormScreen({super.key, this.contactId});

  final String? contactId;

  bool get isEditMode => contactId != null;

  @override
  ConsumerState<ContactFormScreen> createState() => _ContactFormScreenState();
}

class _ContactFormScreenState extends ConsumerState<ContactFormScreen> {
  final _formKey = GlobalKey<FormState>();

  final _firstName = TextEditingController();
  final _lastName = TextEditingController();
  final _email = TextEditingController();
  final _phone = TextEditingController();
  final _title = TextEditingController();
  final _department = TextEditingController();
  final _organization = TextEditingController();
  final _linkedin = TextEditingController();
  final _addressLine = TextEditingController();
  final _city = TextEditingController();
  final _state = TextEditingController();
  final _postcode = TextEditingController();
  final _country = TextEditingController();
  final _description = TextEditingController();

  bool _doNotCall = false;
  String? _accountId;

  Contact? _existing;
  String? _baseline;
  bool _fetching = false;
  bool _saving = false;
  String? _fetchError;

  @override
  void initState() {
    super.initState();
    if (widget.isEditMode) {
      _fetch();
    } else {
      _baseline = _snapshot();
    }
  }

  @override
  void dispose() {
    for (final c in [
      _firstName,
      _lastName,
      _email,
      _phone,
      _title,
      _department,
      _organization,
      _linkedin,
      _addressLine,
      _city,
      _state,
      _postcode,
      _country,
      _description,
    ]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _fetch() async {
    setState(() {
      _fetching = true;
      _fetchError = null;
    });
    final contact = await ref
        .read(contactsProvider.notifier)
        .getContact(widget.contactId!);
    if (!mounted) return;
    setState(() {
      _fetching = false;
      if (contact == null) {
        _fetchError = 'Could not load this contact';
      } else {
        _existing = contact;
        _populate(contact);
      }
    });
    _baseline = _snapshot();
  }

  void _populate(Contact contact) {
    _firstName.text = contact.firstName;
    _lastName.text = contact.lastName;
    _email.text = contact.email ?? '';
    _phone.text = contact.phone ?? '';
    _title.text = contact.title ?? '';
    _department.text = contact.department ?? '';
    _organization.text = contact.organization ?? '';
    _linkedin.text = contact.linkedinUrl ?? '';
    _addressLine.text = contact.addressLine ?? '';
    _city.text = contact.city ?? '';
    _state.text = contact.state ?? '';
    _postcode.text = contact.postcode ?? '';
    _country.text = contact.country ?? '';
    _description.text = contact.description ?? '';
    _doNotCall = contact.doNotCall;
    _accountId = contact.accountId;
  }

  Map<String, dynamic> _buildPayload() {
    final base =
        _existing ?? const Contact(id: '', firstName: '', lastName: '');
    return base
        .copyWith(
          firstName: _firstName.text,
          lastName: _lastName.text,
          email: _email.text,
          phone: _phone.text,
          title: _title.text,
          department: _department.text,
          organization: _organization.text,
          linkedinUrl: _linkedin.text,
          addressLine: _addressLine.text,
          city: _city.text,
          state: _state.text,
          postcode: _postcode.text,
          country: _country.text,
          description: _description.text,
          doNotCall: _doNotCall,
          accountId: _accountId,
          clearAccount: _accountId == null,
        )
        .toPayload();
  }

  String _snapshot() => _buildPayload().toString();

  bool get _hasUnsavedChanges => _baseline != null && _snapshot() != _baseline;

  Future<void> _submit() async {
    if (_saving) return;
    if (!(_formKey.currentState?.validate() ?? false)) return;

    setState(() => _saving = true);
    final notifier = ref.read(contactsProvider.notifier);
    final payload = _buildPayload();

    final String? failure;
    if (widget.isEditMode) {
      failure = await notifier.updateContact(widget.contactId!, payload);
    } else {
      failure = (await notifier.createContact(payload)).error;
    }

    if (!mounted) return;
    setState(() => _saving = false);

    if (failure != null) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(failure), backgroundColor: AppColors.danger600),
      );
      return;
    }
    _baseline = _snapshot();
    context.pop();
  }

  @override
  Widget build(BuildContext context) {
    return UnsavedChangesGuard(
      hasUnsavedChanges: () => _hasUnsavedChanges,
      isSaving: _saving,
      child: Scaffold(
        backgroundColor: AppColors.surface,
        appBar: AppBar(
          title: Text(widget.isEditMode ? 'Edit Contact' : 'New Contact'),
          backgroundColor: AppColors.surface,
          elevation: 0,
          scrolledUnderElevation: 1,
          leading: IconButton(
            icon: const Icon(LucideIcons.chevronLeft),
            onPressed: _saving
                ? null
                : () =>
                      leaveForm(context, hasUnsavedChanges: _hasUnsavedChanges),
          ),
        ),
        body: _body(),
      ),
    );
  }

  Widget _body() {
    if (_fetching) return const Center(child: CircularProgressIndicator());
    if (_fetchError != null) {
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(LucideIcons.alertCircle, size: 40, color: AppColors.danger500),
            const SizedBox(height: 12),
            Text(_fetchError!, style: AppTypography.body),
            const SizedBox(height: 16),
            FilledButton(onPressed: _fetch, child: const Text('Retry')),
          ],
        ),
      );
    }

    final accounts = ref.watch(accountOptionsProvider);

    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Form(
        key: _formKey,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _field(
              _firstName,
              'First name',
              autofocus: !widget.isEditMode,
              validator: (v) => (v?.trim().isEmpty ?? true)
                  ? 'A contact needs a first name'
                  : null,
            ),
            _field(
              _lastName,
              'Last name',
              validator: (v) => (v?.trim().isEmpty ?? true)
                  ? 'A contact needs a last name'
                  : null,
            ),
            _field(
              _email,
              'Email',
              keyboardType: TextInputType.emailAddress,
              validator: (value) {
                final email = value?.trim() ?? '';
                if (email.isEmpty) return null;
                // The server also rejects a duplicate within the org, which
                // this cannot know. That message is surfaced from the response.
                return email.contains('@')
                    ? null
                    : 'That does not look like an email address';
              },
            ),
            _field(_phone, 'Phone', keyboardType: TextInputType.phone),
            _accountPicker(accounts),
            _field(_title, 'Job title'),
            _field(_department, 'Department'),
            _field(_organization, 'Company'),
            _field(_linkedin, 'LinkedIn URL', keyboardType: TextInputType.url),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              value: _doNotCall,
              onChanged: _saving
                  ? null
                  : (value) => setState(() => _doNotCall = value),
              title: const Text('Do not call'),
              subtitle: Text(
                'Shown as a warning on the contact',
                style: AppTypography.caption.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
            ),
            const SizedBox(height: 8),
            Text(
              'Address',
              style: AppTypography.label.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(height: 8),
            _field(_addressLine, 'Street'),
            _field(_city, 'City'),
            _field(_state, 'State'),
            _field(_postcode, 'Postcode'),
            _field(
              _country,
              'Country code',
              hint: 'Two letters, e.g. IN',
              validator: (value) {
                final code = value?.trim() ?? '';
                if (code.isEmpty) return null;
                return RegExp(r'^[A-Za-z]{2}$').hasMatch(code)
                    ? null
                    : 'Use the two-letter code, e.g. IN';
              },
            ),
            _field(_description, 'Notes', maxLines: 4),
            const SizedBox(height: 24),
            SizedBox(
              width: double.infinity,
              child: FilledButton(
                onPressed: _saving ? null : _submit,
                child: _saving
                    ? const SizedBox(
                        height: 18,
                        width: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : Text(
                        widget.isEditMode ? 'Save changes' : 'Create contact',
                      ),
              ),
            ),
            const SizedBox(height: 48),
          ],
        ),
      ),
    );
  }

  /// The account link.
  ///
  /// "No account" is a real, selectable option rather than the absence of a
  /// choice, because a select whose value matches no option submits its first
  /// entry, which would attach the contact to whichever company happens to
  /// sort first.
  ///
  /// The account the contact is already linked to is always an item, even
  /// when the lookup does not carry it: one the user cannot open (the lookup
  /// holds only those they can), a closed one, or one past the lookup cap.
  /// Otherwise the picker reads "No account" while the save keeps the link.
  /// The server keeps an unchanged link whoever saves, and refuses a newly
  /// chosen account the user cannot open.
  Widget _accountPicker(List<AccountLookup> lookup) {
    final storedId = _existing?.accountId;
    final accounts = storedId == null || lookup.any((a) => a.id == storedId)
        ? lookup
        : [
            AccountLookup(
              id: storedId,
              name: _existing?.accountName ?? 'Current account',
            ),
            ...lookup,
          ];
    final ids = accounts.map((a) => a.id).toSet();
    final value = ids.contains(_accountId) ? _accountId : null;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: DropdownButtonFormField<String?>(
        initialValue: value,
        isExpanded: true,
        decoration: const InputDecoration(
          labelText: 'Account',
          border: OutlineInputBorder(),
        ),
        items: [
          const DropdownMenuItem<String?>(
            value: null,
            child: Text('No account'),
          ),
          ...accounts.map(
            (a) => DropdownMenuItem<String?>(
              value: a.id,
              child: Text(a.name, overflow: TextOverflow.ellipsis),
            ),
          ),
        ],
        onChanged: _saving ? null : (next) => setState(() => _accountId = next),
      ),
    );
  }

  Widget _field(
    TextEditingController controller,
    String label, {
    String? hint,
    bool autofocus = false,
    int maxLines = 1,
    TextInputType? keyboardType,
    String? Function(String?)? validator,
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: TextFormField(
        controller: controller,
        autofocus: autofocus,
        enabled: !_saving,
        maxLines: maxLines,
        keyboardType: keyboardType,
        validator: validator,
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          border: const OutlineInputBorder(),
        ),
      ),
    );
  }
}
