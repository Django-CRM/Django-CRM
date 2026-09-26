import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/account.dart';
import '../../providers/accounts_provider.dart';
import '../../providers/duplicates_provider.dart';
import '../../widgets/duplicates/duplicate_notice.dart';
import '../../widgets/forms/unsaved_changes.dart';

/// Create or edit an account.
///
/// The unsaved-changes guard compares `toPayload()` against a snapshot taken
/// when the form finished loading, which is the shape the other six forms were
/// converted to. A field added to the payload is covered without anyone
/// remembering to add it to a comparison.
class AccountFormScreen extends ConsumerStatefulWidget {
  const AccountFormScreen({super.key, this.accountId});

  final String? accountId;

  bool get isEditMode => accountId != null;

  @override
  ConsumerState<AccountFormScreen> createState() => _AccountFormScreenState();
}

class _AccountFormScreenState extends ConsumerState<AccountFormScreen> {
  final _formKey = GlobalKey<FormState>();

  final _name = TextEditingController();
  final _email = TextEditingController();
  final _phone = TextEditingController();
  final _website = TextEditingController();
  final _industry = TextEditingController();
  final _employees = TextEditingController();
  final _addressLine = TextEditingController();
  final _city = TextEditingController();
  final _state = TextEditingController();
  final _postcode = TextEditingController();
  final _country = TextEditingController();
  final _description = TextEditingController();

  Account? _existing;
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
      _name,
      _email,
      _phone,
      _website,
      _industry,
      _employees,
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
    final account = await ref
        .read(accountsProvider.notifier)
        .getAccount(widget.accountId!);
    if (!mounted) return;
    setState(() {
      _fetching = false;
      if (account == null) {
        _fetchError = 'Could not load this account';
      } else {
        _existing = account;
        _populate(account);
      }
    });
    // After the fields hold the saved values, so an untouched edit form is
    // clean rather than dirty against an empty baseline.
    _baseline = _snapshot();
  }

  void _populate(Account account) {
    _name.text = account.name;
    _email.text = account.email ?? '';
    _phone.text = account.phone ?? '';
    _website.text = account.website ?? '';
    _industry.text = account.industry ?? '';
    _employees.text = account.numberOfEmployees?.toString() ?? '';
    _addressLine.text = account.addressLine ?? '';
    _city.text = account.city ?? '';
    _state.text = account.state ?? '';
    _postcode.text = account.postcode ?? '';
    _country.text = account.country ?? '';
    _description.text = account.description ?? '';
  }

  Map<String, dynamic> _buildPayload() {
    final base = _existing ?? const Account(id: '', name: '');
    return base
        .copyWith(
          name: _name.text,
          email: _email.text,
          phone: _phone.text,
          website: _website.text,
          industry: _industry.text,
          numberOfEmployees: int.tryParse(_employees.text.trim()),
          clearNumberOfEmployees: _employees.text.trim().isEmpty,
          addressLine: _addressLine.text,
          city: _city.text,
          state: _state.text,
          postcode: _postcode.text,
          country: _country.text,
          description: _description.text,
        )
        .toPayload();
  }

  String _snapshot() => _buildPayload().toString();

  bool get _hasUnsavedChanges => _baseline != null && _snapshot() != _baseline;

  Future<void> _submit() async {
    if (_saving) return;
    if (!(_formKey.currentState?.validate() ?? false)) return;

    setState(() => _saving = true);
    final notifier = ref.read(accountsProvider.notifier);
    final payload = _buildPayload();

    final String? failure;
    if (widget.isEditMode) {
      failure = await notifier.updateAccount(widget.accountId!, payload);
    } else {
      final result = await notifier.createAccount(payload);
      failure = result.error;
    }

    if (!mounted) return;
    setState(() => _saving = false);

    if (failure != null) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(failure), backgroundColor: AppColors.danger600),
      );
      return;
    }
    // Clean, so leaving does not prompt about work that is already saved.
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
          title: Text(widget.isEditMode ? 'Edit Account' : 'New Account'),
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

    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Form(
        key: _formKey,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _field(
              _name,
              'Account name',
              autofocus: !widget.isEditMode,
              validator: (value) {
                final name = value?.trim() ?? '';
                if (name.isEmpty) return 'Give the account a name';
                // `Account.name` is max_length=64 on the model. The server is
                // what refuses; this saves the round trip and the lost typing.
                if (name.length > 64) {
                  return 'Keep the name under 65 characters';
                }
                return null;
              },
            ),
            _field(
              _email,
              'Email',
              keyboardType: TextInputType.emailAddress,
              validator: (value) {
                final email = value?.trim() ?? '';
                if (email.isEmpty) return null;
                return email.contains('@')
                    ? null
                    : 'That does not look like an email address';
              },
            ),
            _field(_phone, 'Phone', keyboardType: TextInputType.phone),
            _field(_website, 'Website', keyboardType: TextInputType.url),
            if (!widget.isEditMode)
              DuplicateNotice(
                module: DuplicateModule.accounts,
                fields: {
                  'name': _name,
                  'email': _email,
                  'phone': _phone,
                  'website': _website,
                },
              ),
            _field(_industry, 'Industry'),
            _field(
              _employees,
              'Number of employees',
              keyboardType: TextInputType.number,
              validator: (value) {
                final raw = value?.trim() ?? '';
                if (raw.isEmpty) return null;
                final parsed = int.tryParse(raw);
                if (parsed == null) return 'Whole numbers only';
                if (parsed < 0) return 'That cannot be negative';
                return null;
              },
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
                // The column stores an ISO 3166 alpha-2 code. Sending a full
                // country name is a 400 the user cannot act on.
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
                        widget.isEditMode ? 'Save changes' : 'Create account',
                      ),
              ),
            ),
            const SizedBox(height: 48),
          ],
        ),
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
