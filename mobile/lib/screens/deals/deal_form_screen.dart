import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import '../../core/theme/theme.dart';
import '../../data/models/models.dart';
import '../../providers/auth_provider.dart';
import '../../providers/deal_pipelines_provider.dart';
import '../../providers/deals_provider.dart';
import '../../providers/lookup_provider.dart';
import '../../widgets/common/common.dart';
import '../../widgets/forms/unsaved_changes.dart';
import '../../widgets/forms/custom_fields_form.dart';

/// Deal Form Screen - Reusable for both Create and Edit
class DealFormScreen extends ConsumerStatefulWidget {
  final String? dealId;
  final Deal? initialDeal;
  // Pre-fills the Account field when the user lands here from an Account's
  // "New Opportunity" action. Mirrors web's ?accountId= query param.
  final String? accountId;

  const DealFormScreen({
    super.key,
    this.dealId,
    this.initialDeal,
    this.accountId,
  });

  bool get isEditMode => dealId != null;

  @override
  ConsumerState<DealFormScreen> createState() => _DealFormScreenState();
}

class _DealFormScreenState extends ConsumerState<DealFormScreen> {
  final _formKey = GlobalKey<FormState>();
  final _scrollController = ScrollController();
  final _nameController = TextEditingController();
  final _amountController = TextEditingController();
  final _probabilityController = TextEditingController();
  final _notesController = TextEditingController();

  // Field keys for scroll-to-first-error.
  final _nameKey = GlobalKey();
  final _amountKey = GlobalKey();
  final _probabilityKey = GlobalKey();

  // The deal's pipeline and stage code. A new deal starts with neither and
  // takes the default pipeline's first open stage once the pipelines load;
  // sent before then, the server makes the same choice.
  String? _pipelineId;
  String _stageCode = '';
  // Default to "Not Specified", matches web's empty placeholder. The backend
  // column is nullable; auto-stamping NEW_BUSINESS on every create silently
  // miscategorizes data.
  OpportunityType _opportunityType = OpportunityType.unspecified;
  OpportunitySource _leadSource = OpportunitySource.none;
  Currency _currency = Currency.usd;
  DateTime? _closeDate;
  bool _isLoading = false;
  bool _isFetchingDeal = false;
  String? _fetchError;
  Deal? _existingDeal;

  // Selector state
  String? _selectedAccountId;
  List<String> _selectedContactIds = [];
  List<String> _selectedAssignedToIds = [];
  List<String> _selectedTeamIds = [];
  List<String> _selectedTagIds = [];

  // Custom fields, schema-driven JSONField on Opportunity. Loaded lazily so
  // the form renders before the lookup resolves.
  List<CustomFieldDefinition> _customFieldDefs = const [];
  Map<String, dynamic> _customFieldValues = {};

  // Tracks whether the user manually edited the probability. Once true, the
  // stage picker stops overwriting it (otherwise switching stages clobbers a
  // typed value).
  bool _probabilityUserEdited = false;

  // Same idea for the close date, once the user picks one explicitly, stage
  // changes stop auto-suggesting. Clearing the date via the X button resets
  // this so the next stage change can suggest again.
  bool _closeDateUserEdited = false;

  // "Save & Add Another", when set, on a successful create we reset the form
  // instead of popping. Edit mode never shows the button.
  bool _saveAndAddAnother = false;

  // Server-side validation error attached to the Name field. Currently used
  // for the backend's "Opportunity already exists with this name" check; the
  // validator returns this when set, then we clear it on the next edit.
  String? _serverNameError;

  List<DealPipeline> get _pipelines =>
      ref.read(dealPipelinesProvider).value ?? const [];
  DealPipeline? get _pipeline => dealPipelineOf(_pipelines, _pipelineId);
  DealPipelineStage? get _stage => _pipeline?.stageByCode(_stageCode);

  /// What the chosen stage means. Before the pipelines load, the kind the
  /// deal arrived with; a new deal is open either way.
  String get _stageKind =>
      _stage?.kind ??
      (_stageCode == _existingDeal?.stage ? _existingDeal?.stageKind : null) ??
      dealStageOpen;

  String get _stageLabel =>
      _stage?.label ??
      (_stageCode == _existingDeal?.stage ? _existingDeal?.stageLabel : null) ??
      (_stageCode.isEmpty
          ? 'First open stage'
          : legacyDealStageLabel(_stageCode));

  Color get _stageColor {
    final stage = _stage;
    final pipeline = _pipeline;
    if (stage != null && pipeline != null) return pipeline.colorOf(stage);
    return dealStageColor(_stageKind, 0);
  }

  int get _defaultProbability => dealStageProbability(_stageCode, _stageKind);

  /// Point the form at [stage], carrying the probability and the suggested
  /// close date along unless the user has set them. Callers wrap it in
  /// `setState`.
  void _applyStage(DealPipelineStage? stage) {
    final previousDefault = _defaultProbability.toString();
    _stageCode = stage?.code ?? '';
    // Auto-update probability only when the user hasn't typed anything yet,
    // or when the current value is still the previous stage's default (so
    // an edit is never clobbered).
    final current = _probabilityController.text.trim();
    if (!_probabilityUserEdited || current == previousDefault) {
      _probabilityController.text = _defaultProbability.toString();
      _probabilityUserEdited = false;
    }
    // Re-suggest the close date from the new stage's expected days, but only
    // on a new deal, and only while the user hasn't picked one themselves.
    if (!widget.isEditMode && !_closeDateUserEdited) {
      _closeDate = _suggestedCloseDateFor(stage);
    }
  }

  /// A new deal lands in the default pipeline's first open stage, once the
  /// pipelines are known. Loading them is no edit, so a form nobody touched
  /// stays clean.
  void _adoptDefaultPipeline(List<DealPipeline> pipelines) {
    if (widget.isEditMode || _pipelineId != null || _stageCode.isNotEmpty) {
      return;
    }
    final pipeline = activeDealPipeline(pipelines, null);
    if (pipeline == null) return;
    final clean = !_hasUnsavedChanges;
    _pipelineId = pipeline.id;
    _applyStage(pipeline.firstOpenStage);
    if (clean && _baseline != null) _baseline = _snapshot();
  }

  @override
  void initState() {
    super.initState();
    _probabilityController.text = _defaultProbability.toString();
    ref.listenManual(dealPipelinesProvider, (_, next) {
      final pipelines = next.value;
      if (pipelines != null && mounted) {
        setState(() => _adoptDefaultPipeline(pipelines));
      }
    });

    if (widget.initialDeal != null) {
      _populateFromDeal(widget.initialDeal!);
      _baseline = _snapshot();
    } else if (widget.isEditMode) {
      _fetchDeal();
    } else {
      _applyCreateModeDefaults();
      _adoptDefaultPipeline(_pipelines);
      _baseline = _snapshot();
      WidgetsBinding.instance.addPostFrameCallback((_) {
        _loadCustomFieldDefsForCreate();
      });
    }
  }

  /// Sensible defaults for a brand-new deal: org's currency, a stage-aware
  /// suggested close date, and the account from the route if one was passed.
  void _applyCreateModeDefaults() {
    final org = ref.read(selectedOrgProvider);
    _currency = Currency.fromString(org?.defaultCurrency);
    _closeDate = _suggestedCloseDateFor(_stage);
    if (widget.accountId != null && widget.accountId!.isNotEmpty) {
      _selectedAccountId = widget.accountId;
    }
  }

  /// Pre-fills close date from the stage's expected days. Returns null for a
  /// stage that never ages, closed ones included: those dates are the user's.
  DateTime? _suggestedCloseDateFor(DealPipelineStage? stage) {
    final days = stage?.expectedDays;
    if (days == null) return null;
    return DateTime.now().add(Duration(days: days));
  }

  Future<void> _loadCustomFieldDefsForCreate() async {
    try {
      final defs = await ref.read(
        customFieldDefinitionsProvider('Opportunity').future,
      );
      if (!mounted) return;
      setState(() => _customFieldDefs = defs);
    } catch (_) {
      // Soft-fail: custom fields are optional.
    }
  }

  @override
  void dispose() {
    _scrollController.dispose();
    _nameController.dispose();
    _amountController.dispose();
    _probabilityController.dispose();
    _notesController.dispose();
    super.dispose();
  }

  Future<void> _fetchDeal() async {
    setState(() {
      _isFetchingDeal = true;
      _fetchError = null;
    });

    // Fetch the deal and the org's custom-field schema in parallel so edit
    // mode can render the CF editor without a second loading state.
    final results = await Future.wait([
      ref.read(dealsProvider.notifier).getDeal(widget.dealId!),
      ref
          .read(customFieldDefinitionsProvider('Opportunity').future)
          .catchError((_) => <CustomFieldDefinition>[]),
    ]);

    if (!mounted) return;
    setState(() {
      _isFetchingDeal = false;
      final deal = results[0] as Deal?;
      _customFieldDefs = results[1] as List<CustomFieldDefinition>;
      if (deal != null) {
        _existingDeal = deal;
        _populateFromDeal(deal);
        _baseline = _snapshot();
      } else {
        _fetchError = 'Failed to load deal';
      }
    });
  }

  void _populateFromDeal(Deal deal) {
    _existingDeal = deal;
    _nameController.text = deal.title;
    _amountController.text = deal.value > 0
        ? deal.value.toStringAsFixed(2)
        : '';
    _probabilityController.text = deal.probability.toString();
    // In edit mode the existing values are by definition the source of truth.
    // Treat them as user-edited so stage changes don't clobber them.
    _probabilityUserEdited = true;
    _closeDateUserEdited = deal.closeDate != null;
    _notesController.text = deal.notes ?? '';
    _pipelineId = deal.pipelineId;
    _stageCode = deal.stage;
    _opportunityType = deal.opportunityType;
    _leadSource = deal.leadSource;
    _currency = deal.currency;
    _closeDate = deal.closeDate;
    _selectedAccountId = deal.accountId;
    _selectedContactIds = List.from(deal.contactIds);
    _selectedAssignedToIds = List.from(deal.assignedToIds);
    _selectedTeamIds = List.from(deal.teamIds);
    _selectedTagIds = List.from(deal.tagIds);
    _customFieldValues = Map<String, dynamic>.from(deal.customFieldValues);
  }

  /// The form as it looked when it finished loading, against the payload it
  /// would submit now.
  ///
  /// The hand-listed comparison this replaces ignored stage, currency, close date,
  /// probability, type and source on a new deal, so choosing any of them and
  /// pressing back lost them with no prompt. Comparing the payload means a field cannot be
  /// forgotten: if it is submitted, it counts.
  String? _baseline;

  /// `toJson` is the submitted payload and excludes the fields `_buildDeal`
  /// derives from lookups, so a provider warming up cannot read as an edit. Key order is stable because the same
  /// builder produces every snapshot.
  String _snapshot() => _buildDeal().toJson().toString();

  bool get _hasUnsavedChanges => _baseline != null && _snapshot() != _baseline;

  Deal _buildDeal() {
    final amount = double.tryParse(_amountController.text.trim()) ?? 0.0;
    final probability =
        int.tryParse(_probabilityController.text.trim()) ?? _defaultProbability;

    // Get account name from lookup
    final accounts = ref.read(accountOptionsProvider);
    final account = accounts
        .where((a) => a.id == _selectedAccountId)
        .firstOrNull;

    return Deal(
      id: widget.dealId ?? '',
      title: _nameController.text.trim(),
      value: amount,
      pipelineId: _pipelineId,
      stage: _stageCode,
      probability: probability,
      closeDate: _closeDate,
      companyName: account?.name ?? _existingDeal?.companyName ?? '',
      accountId: _selectedAccountId,
      assignedTo: _existingDeal?.assignedTo ?? '',
      assignedToIds: _selectedAssignedToIds,
      // Backend has no priority field; we preserve whatever the existing
      // deal had so the UI-only flag round-trips.
      priority: _existingDeal?.priority ?? Priority.medium,
      labels: _existingDeal?.labels ?? [],
      tagIds: _selectedTagIds,
      contactIds: _selectedContactIds,
      teamIds: _selectedTeamIds,
      notes: _notesController.text.trim().isEmpty
          ? null
          : _notesController.text.trim(),
      opportunityType: _opportunityType,
      leadSource: _leadSource,
      currency: _currency,
      createdAt: _existingDeal?.createdAt ?? DateTime.now(),
      updatedAt: DateTime.now(),
      customFieldValues: _customFieldValues,
    );
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

    // A won stage needs an amount and any closed stage a close date, by kind,
    // as the serializer rules. Caught here for a useful message instead of a
    // generic 400.
    final closeProblem = dealCloseProblem(
      kind: _stageKind,
      stageLabel: _stageLabel,
      amount: double.tryParse(_amountController.text.trim()),
      closeDate: _closeDate,
    );
    if (closeProblem != null) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(closeProblem.message),
          behavior: SnackBarBehavior.floating,
          backgroundColor: AppColors.danger600,
        ),
      );
      if (closeProblem.field == 'amount') {
        Scrollable.ensureVisible(
          _amountKey.currentContext ?? context,
          alignment: 0.2,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      }
      return;
    }

    // Required custom fields. The form validator can't reach inside the CF
    // editor's bespoke inputs, so we check here before submit.
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

    final deal = _buildDeal();
    final notifier = ref.read(dealsProvider.notifier);

    final response = widget.isEditMode
        ? await notifier.updateDeal(widget.dealId!, deal)
        : await notifier.createDeal(deal);

    if (!mounted) return;
    setState(() => _isLoading = false);

    if (response.success) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            widget.isEditMode
                ? 'Deal updated successfully'
                : 'Deal created successfully',
          ),
          behavior: SnackBarBehavior.floating,
        ),
      );
      if (_saveAndAddAnother && !widget.isEditMode) {
        _resetFormForNextDeal();
      } else {
        context.pop(true);
      }
    } else {
      final err = response.error ?? 'Failed to save deal';
      // Backend rejects duplicate names with this exact phrase. Attach it as
      // a Name-field error so the user sees it inline next to the offending
      // input; revalidate so the form picks it up.
      if (err.toLowerCase().contains('already exists')) {
        setState(() => _serverNameError = err);
        _formKey.currentState?.validate();
        _scrollToFirstError();
      }
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(err),
          behavior: SnackBarBehavior.floating,
          backgroundColor: AppColors.danger600,
        ),
      );
    }
  }

  void _resetFormForNextDeal() {
    _formKey.currentState?.reset();
    setState(() {
      _nameController.clear();
      _amountController.clear();
      _notesController.clear();
      _opportunityType = OpportunityType.unspecified;
      _leadSource = OpportunitySource.none;
      _probabilityUserEdited = false;
      _closeDateUserEdited = false;
      _closeDate = null;
      // The next deal goes into the same pipeline, at its start.
      _applyStage(_pipeline?.firstOpenStage);
      _selectedContactIds = [];
      _selectedAssignedToIds = [];
      _selectedTeamIds = [];
      _selectedTagIds = [];
      _customFieldValues = {};
      _serverNameError = null;
      _saveAndAddAnother = false;
      _existingDeal = null;
      _applyCreateModeDefaults();
    });
    _scrollController.animateTo(
      0,
      duration: const Duration(milliseconds: 300),
      curve: Curves.easeOut,
    );
  }

  void _scrollToFirstError() {
    // Visual order matters: scroll lands on the FIRST failing field.
    final orderedKeys = <GlobalKey>[_nameKey, _amountKey, _probabilityKey];
    for (final key in orderedKeys) {
      final ctx = key.currentContext;
      if (ctx == null) continue;
      bool foundError = false;
      void visit(Element element) {
        if (foundError) return;
        if (element.widget is TextFormField) {
          final state = (element as StatefulElement).state;
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
          title: Text(widget.isEditMode ? 'Edit Deal' : 'New Deal'),
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
        bottomNavigationBar: _isFetchingDeal || _fetchError != null
            ? null
            : _buildStickyBottomBar(),
      ),
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
                label: widget.isEditMode ? 'Update Deal' : 'Create Deal',
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

  Widget _buildBody() {
    if (_isFetchingDeal) {
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
            TextButton(onPressed: _fetchDeal, child: const Text('Retry')),
          ],
        ),
      );
    }

    return Form(
      key: _formKey,
      child: SingleChildScrollView(
        controller: _scrollController,
        padding: const EdgeInsets.fromLTRB(24, 24, 24, 32),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Basic Information Section
            _buildSectionTitle('Basic Information'),
            const SizedBox(height: 16),
            _buildBasicFields(),

            const SizedBox(height: 32),

            // Deal Details Section
            _buildSectionTitle('Deal Details'),
            const SizedBox(height: 16),
            _buildDealDetailsFields(),

            const SizedBox(height: 32),

            // Relationships Section
            _buildSectionTitle('Relationships'),
            const SizedBox(height: 16),
            _buildRelationshipFields(),

            const SizedBox(height: 32),

            // Classification Section
            _buildSectionTitle('Classification'),
            const SizedBox(height: 16),
            _buildClassificationFields(),

            if (_customFieldDefs.isNotEmpty) ...[
              const SizedBox(height: 32),
              _buildSectionTitle('Custom Fields'),
              const SizedBox(height: 16),
              CustomFieldsForm(
                targetModel: 'Opportunity',
                values: _customFieldValues,
                onChanged: (next) => setState(() => _customFieldValues = next),
              ),
            ],

            const SizedBox(height: 32),

            // Notes Section
            _buildSectionTitle('Notes'),
            const SizedBox(height: 16),
            TextAreaField(
              label: 'Notes',
              hint: 'Add any additional notes about this deal...',
              controller: _notesController,
              maxLines: 4,
            ),
          ],
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

  Widget _buildBasicFields() {
    return Column(
      children: [
        // Deal Name
        FloatingLabelInput(
          key: _nameKey,
          label: 'Deal Name *',
          hint: 'Enterprise Contract',
          controller: _nameController,
          prefixIcon: LucideIcons.briefcase,
          maxLength: 255,
          textCapitalization: TextCapitalization.words,
          textInputAction: TextInputAction.next,
          onChanged: (_) {
            // User edited the name. Clear any stale "already exists" hint.
            if (_serverNameError != null) {
              setState(() => _serverNameError = null);
            }
          },
          validator: (value) {
            if (_serverNameError != null) return _serverNameError;
            if (value == null || value.trim().isEmpty) {
              return 'Deal name is required';
            }
            if (value.trim().length > 255) {
              return 'Deal name must be 255 characters or fewer';
            }
            return null;
          },
        ),

        const SizedBox(height: 16),

        // Amount with Currency
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Currency Selector, wider chip showing both symbol and code so
            // it reads as a discoverable dropdown rather than a passive label.
            GestureDetector(
              onTap: _showCurrencyPicker,
              child: Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 12,
                  vertical: 16,
                ),
                decoration: BoxDecoration(
                  color: AppColors.gray100,
                  borderRadius: const BorderRadius.only(
                    topLeft: Radius.circular(12),
                    bottomLeft: Radius.circular(12),
                  ),
                  border: Border.all(color: AppColors.border),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      _currency.symbol,
                      style: AppTypography.body.copyWith(
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(width: 6),
                    Text(
                      _currency.value,
                      style: AppTypography.body.copyWith(
                        fontWeight: FontWeight.w600,
                        color: AppColors.textSecondary,
                      ),
                    ),
                    const SizedBox(width: 4),
                    Icon(
                      LucideIcons.chevronDown,
                      size: 14,
                      color: AppColors.textSecondary,
                    ),
                  ],
                ),
              ),
            ),
            // Amount Input
            Expanded(
              child: TextFormField(
                key: _amountKey,
                controller: _amountController,
                keyboardType: const TextInputType.numberWithOptions(
                  decimal: true,
                ),
                textInputAction: TextInputAction.next,
                inputFormatters: [
                  FilteringTextInputFormatter.allow(RegExp(r'[0-9.]')),
                  // Reject a second decimal point. Without this, the user can
                  // type "1.2.3" and only learn it's invalid at submit.
                  TextInputFormatter.withFunction((oldValue, newValue) {
                    if ('.'.allMatches(newValue.text).length > 1) {
                      return oldValue;
                    }
                    return newValue;
                  }),
                ],
                validator: (value) {
                  final raw = value?.trim() ?? '';
                  if (raw.isEmpty) return null; // amount is optional
                  final n = double.tryParse(raw);
                  if (n == null) return 'Enter a valid number';
                  if (n < 0) return 'Amount must be ≥ 0';
                  // Backend: DecimalField(max_digits=12, decimal_places=2) →
                  // up to 10 digits before the decimal.
                  final intPart = n.truncate().toString();
                  if (intPart.length > 10) return 'Amount is too large';
                  return null;
                },
                decoration: InputDecoration(
                  hintText: '0.00',
                  hintStyle: AppTypography.body.copyWith(
                    color: AppColors.gray400,
                  ),
                  filled: true,
                  fillColor: AppColors.gray50,
                  border: const OutlineInputBorder(
                    borderRadius: BorderRadius.only(
                      topRight: Radius.circular(12),
                      bottomRight: Radius.circular(12),
                    ),
                    borderSide: BorderSide.none,
                  ),
                  enabledBorder: OutlineInputBorder(
                    borderRadius: const BorderRadius.only(
                      topRight: Radius.circular(12),
                      bottomRight: Radius.circular(12),
                    ),
                    borderSide: BorderSide(color: AppColors.border),
                  ),
                  focusedBorder: OutlineInputBorder(
                    borderRadius: const BorderRadius.only(
                      topRight: Radius.circular(12),
                      bottomRight: Radius.circular(12),
                    ),
                    borderSide: BorderSide(
                      color: AppColors.primary500,
                      width: 2,
                    ),
                  ),
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 16,
                  ),
                ),
              ),
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildDealDetailsFields() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Pipeline, only when there is a choice to make.
        if (ref.watch(dealPipelinesProvider).value case final pipelines?
            when pipelines.length > 1) ...[
          _buildDropdownField(
            label: 'Pipeline',
            value: _pipeline?.name ?? 'Default pipeline',
            onTap: () => _showPipelinePicker(pipelines),
          ),
          const SizedBox(height: 16),
        ],

        // Stage Dropdown
        _buildDropdownField(
          label: 'Stage',
          value: _stageLabel,
          color: _stageColor,
          onTap: _showStagePicker,
        ),

        const SizedBox(height: 16),

        // Probability
        FloatingLabelInput(
          key: _probabilityKey,
          label: 'Probability (%)',
          hint: '50',
          controller: _probabilityController,
          prefixIcon: LucideIcons.percent,
          keyboardType: TextInputType.number,
          textInputAction: TextInputAction.next,
          inputFormatters: [
            FilteringTextInputFormatter.digitsOnly,
            LengthLimitingTextInputFormatter(3),
          ],
          onChanged: (_) {
            // Once the user types anything in here, stop auto-overwriting on
            // stage change. Even clearing the field counts. They're showing
            // intent to manage it themselves.
            if (!_probabilityUserEdited) {
              _probabilityUserEdited = true;
            }
          },
          validator: (value) {
            if (value != null && value.isNotEmpty) {
              final prob = int.tryParse(value);
              if (prob == null || prob < 0 || prob > 100) {
                return 'Probability must be 0-100';
              }
            }
            return null;
          },
        ),

        const SizedBox(height: 16),

        // Close Date
        _buildDateField(
          label: 'Expected Close Date',
          value: _closeDate,
          onTap: _selectCloseDate,
        ),
      ],
    );
  }

  Widget _buildRelationshipFields() {
    final accountsAsync = ref.watch(accountsLookupProvider);
    final contactsAsync = ref.watch(contactsLookupProvider);
    final usersAsync = ref.watch(usersLookupProvider);
    final teamsAsync = ref.watch(teamsLookupProvider);
    final tagsAsync = ref.watch(tagsLookupProvider);

    final accounts = accountsAsync.value ?? const [];
    final contacts = contactsAsync.value ?? const [];
    final users = usersAsync.value ?? const [];
    final teams = teamsAsync.value ?? const [];
    final tags = tagsAsync.value ?? const [];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Account Selector
        _buildSingleSelectField(
          label: 'Account',
          value: _selectedAccountId != null
              ? accounts
                    .where((a) => a.id == _selectedAccountId)
                    .firstOrNull
                    ?.name
              : null,
          placeholder: 'Select account',
          icon: LucideIcons.building2,
          isLoading: accountsAsync.isLoading,
          onTap: _showAccountPicker,
          onClear: _selectedAccountId != null
              ? () => setState(() => _selectedAccountId = null)
              : null,
        ),

        const SizedBox(height: 16),

        // Contacts Multi-Select
        _buildMultiSelectField(
          label: 'Contacts',
          selectedCount: _selectedContactIds.length,
          selectedItems: _selectedContactIds
              .map(
                (id) =>
                    contacts.where((c) => c.id == id).firstOrNull?.fullName ??
                    '',
              )
              .where((name) => name.isNotEmpty)
              .toList(),
          placeholder: 'Select contacts',
          icon: LucideIcons.users,
          isLoading: contactsAsync.isLoading,
          onTap: _showContactsPicker,
        ),

        const SizedBox(height: 16),

        // Assigned To Multi-Select
        _buildMultiSelectField(
          label: 'Assigned To',
          selectedCount: _selectedAssignedToIds.length,
          selectedItems: _selectedAssignedToIds
              .map(
                (id) =>
                    users.where((u) => u.id == id).firstOrNull?.displayName ??
                    '',
              )
              .where((name) => name.isNotEmpty)
              .toList(),
          placeholder: 'Select assignees',
          icon: LucideIcons.userCheck,
          isLoading: usersAsync.isLoading,
          onTap: _showAssignedToPicker,
        ),

        const SizedBox(height: 16),

        // Teams Multi-Select
        _buildMultiSelectField(
          label: 'Teams',
          selectedCount: _selectedTeamIds.length,
          selectedItems: _selectedTeamIds
              .map(
                (id) => teams.where((t) => t.id == id).firstOrNull?.name ?? '',
              )
              .where((name) => name.isNotEmpty)
              .toList(),
          placeholder: 'Select teams',
          icon: LucideIcons.users2,
          isLoading: teamsAsync.isLoading,
          onTap: _showTeamsPicker,
        ),

        const SizedBox(height: 16),

        // Tags Multi-Select
        _buildMultiSelectField(
          label: 'Tags',
          selectedCount: _selectedTagIds.length,
          selectedItems: _selectedTagIds
              .map(
                (id) => tags.where((t) => t.id == id).firstOrNull?.name ?? '',
              )
              .where((name) => name.isNotEmpty)
              .toList(),
          placeholder: 'Select tags',
          icon: LucideIcons.tag,
          isLoading: tagsAsync.isLoading,
          onTap: _showTagsPicker,
        ),
      ],
    );
  }

  Widget _buildClassificationFields() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Opportunity Type Dropdown
        _buildDropdownField(
          label: 'Opportunity Type',
          value: _opportunityType.label,
          onTap: _showOpportunityTypePicker,
        ),

        const SizedBox(height: 16),

        // Lead Source Dropdown
        _buildDropdownField(
          label: 'Lead Source',
          value: _leadSource.label,
          onTap: _showLeadSourcePicker,
        ),
      ],
    );
  }

  Widget _buildDropdownField({
    required String label,
    required String value,
    Color? color,
    required VoidCallback onTap,
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
                Expanded(child: Text(value, style: AppTypography.body)),
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

  Widget _buildSingleSelectField({
    required String label,
    required String? value,
    required String placeholder,
    required IconData icon,
    required bool isLoading,
    required VoidCallback onTap,
    VoidCallback? onClear,
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
          onTap: isLoading ? null : onTap,
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
                  child: isLoading
                      ? Row(
                          children: [
                            SizedBox(
                              width: 16,
                              height: 16,
                              child: CircularProgressIndicator(
                                strokeWidth: 2,
                                color: AppColors.textSecondary,
                              ),
                            ),
                            const SizedBox(width: 8),
                            Text(
                              'Loading...',
                              style: AppTypography.body.copyWith(
                                color: AppColors.gray400,
                              ),
                            ),
                          ],
                        )
                      : Text(
                          value ?? placeholder,
                          style: AppTypography.body.copyWith(
                            color: value != null
                                ? AppColors.textPrimary
                                : AppColors.gray400,
                          ),
                        ),
                ),
                if (onClear != null && value != null) ...[
                  GestureDetector(
                    onTap: onClear,
                    child: Icon(
                      LucideIcons.x,
                      size: 18,
                      color: AppColors.textSecondary,
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

  Widget _buildMultiSelectField({
    required String label,
    required int selectedCount,
    required List<String> selectedItems,
    required String placeholder,
    required IconData icon,
    required bool isLoading,
    required VoidCallback onTap,
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
          onTap: isLoading ? null : onTap,
          child: Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
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
                  child: isLoading
                      ? Row(
                          children: [
                            SizedBox(
                              width: 16,
                              height: 16,
                              child: CircularProgressIndicator(
                                strokeWidth: 2,
                                color: AppColors.textSecondary,
                              ),
                            ),
                            const SizedBox(width: 8),
                            Text(
                              'Loading...',
                              style: AppTypography.body.copyWith(
                                color: AppColors.gray400,
                              ),
                            ),
                          ],
                        )
                      : selectedCount > 0
                      ? Wrap(
                          spacing: 4,
                          runSpacing: 4,
                          children: [
                            ...selectedItems
                                .take(2)
                                .map((item) => _buildChip(item)),
                            if (selectedCount > 2)
                              _buildChip(
                                '+${selectedCount - 2} more',
                                isMore: true,
                              ),
                          ],
                        )
                      : Text(
                          placeholder,
                          style: AppTypography.body.copyWith(
                            color: AppColors.gray400,
                          ),
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

  Widget _buildChip(String label, {bool isMore = false}) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: isMore ? AppColors.primary100 : AppColors.gray200,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        label,
        style: AppTypography.caption.copyWith(
          color: isMore ? AppColors.primary700 : AppColors.textPrimary,
          fontWeight: FontWeight.w500,
        ),
      ),
    );
  }

  Widget _buildDateField({
    required String label,
    required DateTime? value,
    required VoidCallback onTap,
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
                Icon(
                  LucideIcons.calendar,
                  size: 20,
                  color: AppColors.textSecondary,
                ),
                const SizedBox(width: 12),
                Text(
                  value != null ? _formatDate(value) : 'Select date',
                  style: AppTypography.body.copyWith(
                    color: value != null
                        ? AppColors.textPrimary
                        : AppColors.gray400,
                  ),
                ),
                const Spacer(),
                if (value != null)
                  GestureDetector(
                    onTap: () => setState(() {
                      _closeDate = null;
                      _closeDateUserEdited = false;
                    }),
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

  String _formatDate(DateTime date) {
    final months = [
      'Jan',
      'Feb',
      'Mar',
      'Apr',
      'May',
      'Jun',
      'Jul',
      'Aug',
      'Sep',
      'Oct',
      'Nov',
      'Dec',
    ];
    return '${months[date.month - 1]} ${date.day}, ${date.year}';
  }

  Future<void> _selectCloseDate() async {
    final now = DateTime.now();
    // Open stages: planning for the future, so reject past dates. Closed
    // stages record what actually happened, so allow back-dating.
    final firstDate = _stageKind != dealStageOpen
        ? DateTime(now.year - 5, now.month, now.day)
        : DateTime(now.year, now.month, now.day);
    final lastDate = DateTime(now.year + 5, now.month, now.day);
    var initial = _closeDate ?? now;
    if (initial.isBefore(firstDate)) initial = firstDate;
    if (initial.isAfter(lastDate)) initial = lastDate;
    final date = await showDatePicker(
      context: context,
      initialDate: initial,
      firstDate: firstDate,
      lastDate: lastDate,
    );
    if (date != null) {
      setState(() {
        _closeDate = date;
        // Once the user explicitly picks a date, stop auto-suggesting on stage
        // changes. They've expressed intent. Clearing the date (X button)
        // resets this so the next stage change can suggest again.
        _closeDateUserEdited = true;
      });
    }
  }

  /// Moving a deal to another pipeline has to name a stage there (the server
  /// refuses otherwise), so the stage resets to that pipeline's first open
  /// one.
  void _showPipelinePicker(List<DealPipeline> pipelines) {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _PickerBottomSheet(
        title: 'Select Pipeline',
        options: [
          for (final pipeline in pipelines)
            _PickerOption(
              label: pipeline.name,
              subtitle: pipeline.isDefault ? 'Default' : null,
              isSelected: _pipeline?.id == pipeline.id,
              onTap: () {
                if (_pipeline?.id != pipeline.id) {
                  setState(() {
                    _pipelineId = pipeline.id;
                    _applyStage(pipeline.firstOpenStage);
                  });
                }
                Navigator.pop(context);
              },
            ),
        ],
      ),
    );
  }

  void _showStagePicker() {
    final pipeline = _pipeline;
    if (pipeline == null) {
      // Not loaded yet, or the load failed: ask again rather than offer a
      // list of stages this deal's pipeline may not have.
      ref.read(dealPipelinesProvider.notifier).refresh();
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('The stages are still loading. Try again shortly.'),
          behavior: SnackBarBehavior.floating,
        ),
      );
      return;
    }
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _PickerBottomSheet(
        title: 'Select Stage',
        options: [
          for (final stage in pipeline.stages)
            _PickerOption(
              label: stage.label,
              subtitle: stage.isOpen ? null : dealStageKinds[stage.kind],
              isSelected: _stageCode == stage.code,
              color: pipeline.colorOf(stage),
              onTap: () {
                setState(() => _applyStage(stage));
                Navigator.pop(context);
              },
            ),
        ],
      ),
    );
  }

  void _showOpportunityTypePicker() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _PickerBottomSheet(
        title: 'Select Opportunity Type',
        options: OpportunityType.values
            .map(
              (type) => _PickerOption(
                label: type.label,
                isSelected: _opportunityType == type,
                onTap: () {
                  setState(() => _opportunityType = type);
                  Navigator.pop(context);
                },
              ),
            )
            .toList(),
      ),
    );
  }

  void _showLeadSourcePicker() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _PickerBottomSheet(
        title: 'Select Lead Source',
        options: OpportunitySource.values
            .map(
              (source) => _PickerOption(
                label: source.label,
                isSelected: _leadSource == source,
                onTap: () {
                  setState(() => _leadSource = source);
                  Navigator.pop(context);
                },
              ),
            )
            .toList(),
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
        options: Currency.values
            .map(
              (curr) => _PickerOption(
                label: '${curr.label} (${curr.symbol})',
                isSelected: _currency == curr,
                onTap: () {
                  setState(() => _currency = curr);
                  Navigator.pop(context);
                },
              ),
            )
            .toList(),
      ),
    );
  }

  void _showAccountPicker() {
    final accounts = ref.read(accountOptionsProvider);
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => DraggableScrollableSheet(
        initialChildSize: 0.6,
        minChildSize: 0.3,
        maxChildSize: 0.9,
        expand: false,
        builder: (context, scrollController) => _SearchablePickerSheet(
          title: 'Select Account',
          searchHint: 'Search accounts...',
          items: accounts,
          itemBuilder: (account) => _PickerOption(
            label: account.name,
            subtitle: account.website,
            isSelected: _selectedAccountId == account.id,
            onTap: () {
              setState(() => _selectedAccountId = account.id);
              Navigator.pop(context);
            },
          ),
          searchMatcher: (account, query) =>
              account.name.toLowerCase().contains(query.toLowerCase()),
          scrollController: scrollController,
          emptyMessage: 'No accounts found',
        ),
      ),
    );
  }

  void _showContactsPicker() {
    final contacts = ref.read(contactOptionsProvider);
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => DraggableScrollableSheet(
        initialChildSize: 0.6,
        minChildSize: 0.3,
        maxChildSize: 0.9,
        expand: false,
        builder: (context, scrollController) =>
            _MultiSelectPickerSheet<ContactLookup>(
              title: 'Select Contacts',
              searchHint: 'Search contacts...',
              items: contacts,
              selectedIds: _selectedContactIds,
              getId: (c) => c.id,
              getLabel: (c) => c.fullName,
              getSubtitle: (c) => c.email,
              searchMatcher: (contact, query) =>
                  contact.fullName.toLowerCase().contains(
                    query.toLowerCase(),
                  ) ||
                  (contact.email?.toLowerCase().contains(query.toLowerCase()) ??
                      false),
              scrollController: scrollController,
              emptyMessage: 'No contacts found',
              onDone: (selectedIds) {
                setState(() => _selectedContactIds = selectedIds);
              },
            ),
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
      builder: (context) => DraggableScrollableSheet(
        initialChildSize: 0.6,
        minChildSize: 0.3,
        maxChildSize: 0.9,
        expand: false,
        builder: (context, scrollController) =>
            _MultiSelectPickerSheet<UserLookup>(
              title: 'Assign To',
              searchHint: 'Search users...',
              items: users,
              selectedIds: _selectedAssignedToIds,
              getId: (u) => u.id,
              getLabel: (u) => u.displayName,
              getSubtitle: (u) => u.email,
              searchMatcher: (user, query) =>
                  user.displayName.toLowerCase().contains(
                    query.toLowerCase(),
                  ) ||
                  user.email.toLowerCase().contains(query.toLowerCase()),
              scrollController: scrollController,
              emptyMessage: 'No users found',
              onDone: (selectedIds) {
                setState(() => _selectedAssignedToIds = selectedIds);
              },
            ),
      ),
    );
  }

  void _showTeamsPicker() {
    final teams = ref.read(teamsProvider);
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => DraggableScrollableSheet(
        initialChildSize: 0.6,
        minChildSize: 0.3,
        maxChildSize: 0.9,
        expand: false,
        builder: (context, scrollController) =>
            _MultiSelectPickerSheet<TeamLookup>(
              title: 'Select Teams',
              searchHint: 'Search teams...',
              items: teams,
              selectedIds: _selectedTeamIds,
              getId: (t) => t.id,
              getLabel: (t) => t.name,
              getSubtitle: (t) => t.description,
              searchMatcher: (team, query) =>
                  team.name.toLowerCase().contains(query.toLowerCase()),
              scrollController: scrollController,
              emptyMessage: 'No teams found',
              onDone: (selectedIds) {
                setState(() => _selectedTeamIds = selectedIds);
              },
            ),
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
      builder: (context) => DraggableScrollableSheet(
        initialChildSize: 0.6,
        minChildSize: 0.3,
        maxChildSize: 0.9,
        expand: false,
        builder: (context, scrollController) =>
            _MultiSelectPickerSheet<TagLookup>(
              title: 'Select Tags',
              searchHint: 'Search tags...',
              items: tags,
              selectedIds: _selectedTagIds,
              getId: (t) => t.id,
              getLabel: (t) => t.name,
              getSubtitle: null,
              searchMatcher: (tag, query) =>
                  tag.name.toLowerCase().contains(query.toLowerCase()),
              scrollController: scrollController,
              emptyMessage: 'No tags found',
              onDone: (selectedIds) {
                setState(() => _selectedTagIds = selectedIds);
              },
              tagColors: {for (var t in tags) t.id: t.color},
            ),
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
  final String? subtitle;
  final bool isSelected;
  final Color? color;
  final VoidCallback onTap;

  const _PickerOption({
    required this.label,
    this.subtitle,
    required this.isSelected,
    this.color,
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
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    label,
                    style: AppTypography.body.copyWith(
                      fontWeight: isSelected
                          ? FontWeight.w600
                          : FontWeight.normal,
                      color: isSelected
                          ? AppColors.primary600
                          : AppColors.textPrimary,
                    ),
                  ),
                  if (subtitle != null && subtitle!.isNotEmpty)
                    Text(
                      subtitle!,
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textSecondary,
                      ),
                    ),
                ],
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

/// Searchable picker sheet for single selection
class _SearchablePickerSheet<T> extends StatefulWidget {
  final String title;
  final String searchHint;
  final List<T> items;
  final Widget Function(T item) itemBuilder;
  final bool Function(T item, String query) searchMatcher;
  final ScrollController scrollController;
  final String emptyMessage;

  const _SearchablePickerSheet({
    required this.title,
    required this.searchHint,
    required this.items,
    required this.itemBuilder,
    required this.searchMatcher,
    required this.scrollController,
    required this.emptyMessage,
  });

  @override
  State<_SearchablePickerSheet<T>> createState() =>
      _SearchablePickerSheetState<T>();
}

class _SearchablePickerSheetState<T> extends State<_SearchablePickerSheet<T>> {
  final _searchController = TextEditingController();
  List<T> _filteredItems = [];

  @override
  void initState() {
    super.initState();
    _filteredItems = widget.items;
    _searchController.addListener(_onSearchChanged);
  }

  @override
  void dispose() {
    _searchController.removeListener(_onSearchChanged);
    _searchController.dispose();
    super.dispose();
  }

  void _onSearchChanged() {
    final query = _searchController.text;
    setState(() {
      if (query.isEmpty) {
        _filteredItems = widget.items;
      } else {
        _filteredItems = widget.items
            .where((item) => widget.searchMatcher(item, query))
            .toList();
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return Column(
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
            controller: _searchController,
            decoration: InputDecoration(
              hintText: widget.searchHint,
              prefixIcon: Icon(LucideIcons.search, size: 20),
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
          ),
        ),
        const SizedBox(height: 8),
        Expanded(
          child: _filteredItems.isEmpty
              ? Center(
                  child: Text(
                    widget.emptyMessage,
                    style: AppTypography.body.copyWith(
                      color: AppColors.textSecondary,
                    ),
                  ),
                )
              : ListView.builder(
                  controller: widget.scrollController,
                  itemCount: _filteredItems.length,
                  itemBuilder: (context, index) =>
                      widget.itemBuilder(_filteredItems[index]),
                ),
        ),
      ],
    );
  }
}

/// Multi-select picker sheet
class _MultiSelectPickerSheet<T> extends StatefulWidget {
  final String title;
  final String searchHint;
  final List<T> items;
  final List<String> selectedIds;
  final String Function(T item) getId;
  final String Function(T item) getLabel;
  final String? Function(T item)? getSubtitle;
  final bool Function(T item, String query) searchMatcher;
  final ScrollController scrollController;
  final String emptyMessage;
  final void Function(List<String> selectedIds) onDone;
  final Map<String, String>? tagColors;

  const _MultiSelectPickerSheet({
    required this.title,
    required this.searchHint,
    required this.items,
    required this.selectedIds,
    required this.getId,
    required this.getLabel,
    this.getSubtitle,
    required this.searchMatcher,
    required this.scrollController,
    required this.emptyMessage,
    required this.onDone,
    this.tagColors,
  });

  @override
  State<_MultiSelectPickerSheet<T>> createState() =>
      _MultiSelectPickerSheetState<T>();
}

class _MultiSelectPickerSheetState<T>
    extends State<_MultiSelectPickerSheet<T>> {
  final _searchController = TextEditingController();
  List<T> _filteredItems = [];
  late List<String> _selectedIds;

  @override
  void initState() {
    super.initState();
    _filteredItems = widget.items;
    _selectedIds = List.from(widget.selectedIds);
    _searchController.addListener(_onSearchChanged);
  }

  @override
  void dispose() {
    _searchController.removeListener(_onSearchChanged);
    _searchController.dispose();
    super.dispose();
  }

  void _onSearchChanged() {
    final query = _searchController.text;
    setState(() {
      if (query.isEmpty) {
        _filteredItems = widget.items;
      } else {
        _filteredItems = widget.items
            .where((item) => widget.searchMatcher(item, query))
            .toList();
      }
    });
  }

  void _toggleSelection(String id) {
    setState(() {
      if (_selectedIds.contains(id)) {
        _selectedIds.remove(id);
      } else {
        _selectedIds.add(id);
      }
    });
  }

  Color _getTagColor(String colorName) {
    final colors = {
      'gray': AppColors.gray400,
      'red': AppColors.danger500,
      'orange': AppColors.warning500,
      'amber': const Color(0xFFF59E0B),
      'yellow': const Color(0xFFEAB308),
      'lime': const Color(0xFF84CC16),
      'green': AppColors.success500,
      'emerald': const Color(0xFF10B981),
      'teal': AppColors.teal500,
      'cyan': const Color(0xFF06B6D4),
      'sky': const Color(0xFF0EA5E9),
      'blue': AppColors.primary500,
      'indigo': const Color(0xFF6366F1),
      'violet': AppColors.purple500,
      'purple': const Color(0xFFA855F7),
      'fuchsia': const Color(0xFFD946EF),
      'pink': const Color(0xFFEC4899),
      'rose': const Color(0xFFF43F5E),
    };
    return colors[colorName] ?? AppColors.gray400;
  }

  @override
  Widget build(BuildContext context) {
    return Column(
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
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
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
                  widget.onDone(_selectedIds);
                  Navigator.pop(context);
                },
                child: Text(
                  'Done (${_selectedIds.length})',
                  style: AppTypography.label.copyWith(
                    color: AppColors.primary600,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: TextField(
            controller: _searchController,
            decoration: InputDecoration(
              hintText: widget.searchHint,
              prefixIcon: Icon(LucideIcons.search, size: 20),
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
          ),
        ),
        const SizedBox(height: 8),
        Expanded(
          child: _filteredItems.isEmpty
              ? Center(
                  child: Text(
                    widget.emptyMessage,
                    style: AppTypography.body.copyWith(
                      color: AppColors.textSecondary,
                    ),
                  ),
                )
              : ListView.builder(
                  controller: widget.scrollController,
                  itemCount: _filteredItems.length,
                  itemBuilder: (context, index) {
                    final item = _filteredItems[index];
                    final id = widget.getId(item);
                    final isSelected = _selectedIds.contains(id);
                    final subtitle = widget.getSubtitle?.call(item);
                    final tagColor = widget.tagColors?[id];

                    return InkWell(
                      onTap: () => _toggleSelection(id),
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
                                    : Colors.transparent,
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
                            if (tagColor != null) ...[
                              Container(
                                width: 12,
                                height: 12,
                                decoration: BoxDecoration(
                                  color: _getTagColor(tagColor),
                                  shape: BoxShape.circle,
                                ),
                              ),
                              const SizedBox(width: 8),
                            ],
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    widget.getLabel(item),
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
    );
  }
}
