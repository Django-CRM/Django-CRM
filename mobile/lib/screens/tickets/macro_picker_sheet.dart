import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/macro.dart';
import '../../providers/settings_provider.dart';
import '../../widgets/common/badge.dart';

/// What picking a saved reply produced.
///
/// For a macro with text, [text] is the expansion to insert and [macro] says
/// which actions (if any) ride along as chips until the reply is sent. For a
/// macro without text, nothing is inserted: its actions were applied at once
/// and [appliedSummary] says what happened.
class MacroPick {
  const MacroPick({required this.macro, this.text, this.appliedSummary});

  final Macro macro;
  final String? text;
  final String? appliedSummary;
}

/// Pick a saved reply and expand it against this ticket.
///
/// Returns the pick, or `null` if nothing was chosen. This is the point of
/// the whole feature on a phone: the alternative is typing a paragraph of
/// boilerplate on a phone keyboard.
///
/// The expansion is a server round trip (`POST /macros/<id>/render/`), not a
/// local search and replace. `macros/render.py` owns the supported token set,
/// substituting here would drift from it, and the server is also where the
/// usage count is kept. A macro with no text has nothing to expand, so it is
/// applied (`POST /macros/<id>/apply/`) instead.
Future<MacroPick?> showMacroPickerSheet(BuildContext context, String ticketId) {
  return showModalBottomSheet<MacroPick>(
    context: context,
    isScrollControlled: true,
    builder: (context) => _MacroPickerSheet(ticketId: ticketId),
  );
}

class _MacroPickerSheet extends ConsumerStatefulWidget {
  const _MacroPickerSheet({required this.ticketId});

  final String ticketId;

  @override
  ConsumerState<_MacroPickerSheet> createState() => _MacroPickerSheetState();
}

class _MacroPickerSheetState extends ConsumerState<_MacroPickerSheet> {
  final _search = TextEditingController();
  String _query = '';
  String? _applyingId;
  String? _error;

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _pick(Macro macro) async {
    setState(() {
      _applyingId = macro.id;
      _error = null;
    });
    if (!macro.hasBody) {
      final applied = await applyMacro(
        macroId: macro.id,
        ticketId: widget.ticketId,
      );
      if (!mounted) return;
      if (applied.error != null) {
        setState(() {
          _applyingId = null;
          _error = applied.error;
        });
        return;
      }
      Navigator.of(
        context,
      ).pop(MacroPick(macro: macro, appliedSummary: applied.summary));
      return;
    }
    final result = await renderMacro(
      macroId: macro.id,
      ticketId: widget.ticketId,
    );
    if (!mounted) return;
    if (result.error != null) {
      setState(() {
        _applyingId = null;
        _error = result.error;
      });
      return;
    }
    Navigator.of(context).pop(MacroPick(macro: macro, text: result.text));
  }

  /// Filtered on the client, over a list the server already scoped to the
  /// active macros this person may see. The list is small (org macros plus
  /// your own), so a round trip per keystroke would buy nothing.
  List<Macro> _filter(List<Macro> macros) {
    final q = _query.trim().toLowerCase();
    if (q.isEmpty) return macros;
    return macros
        .where(
          (m) =>
              m.title.toLowerCase().contains(q) ||
              m.body.toLowerCase().contains(q),
        )
        .toList(growable: false);
  }

  @override
  Widget build(BuildContext context) {
    final async = ref.watch(activeMacrosProvider);

    return Padding(
      padding: EdgeInsets.only(
        bottom: MediaQuery.of(context).viewInsets.bottom,
      ),
      child: SizedBox(
        // Half the screen: enough to show several replies, little enough that
        // the ticket stays visible behind it.
        height: MediaQuery.of(context).size.height * 0.6,
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      'Saved replies',
                      style: AppTypography.h3.copyWith(
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                  IconButton(
                    tooltip: 'Close',
                    icon: const Icon(LucideIcons.x, size: 20),
                    onPressed: () => Navigator.of(context).pop(),
                  ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: TextField(
                controller: _search,
                onChanged: (v) => setState(() => _query = v),
                textInputAction: TextInputAction.search,
                decoration: const InputDecoration(
                  hintText: 'Search saved replies',
                  prefixIcon: Icon(LucideIcons.search, size: 18),
                  isDense: true,
                  border: OutlineInputBorder(),
                ),
              ),
            ),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                child: Text(
                  _error!,
                  style: AppTypography.caption.copyWith(
                    color: AppColors.danger600,
                  ),
                ),
              ),
            const SizedBox(height: 8),
            Expanded(
              child: async.when(
                loading: () => const Center(child: CircularProgressIndicator()),
                error: (_, _) => Center(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Text(
                      'Could not load the saved replies',
                      style: AppTypography.body.copyWith(
                        color: AppColors.textSecondary,
                      ),
                      textAlign: TextAlign.center,
                    ),
                  ),
                ),
                data: (macros) {
                  final rows = _filter(macros);
                  if (rows.isEmpty) {
                    return Center(
                      child: Padding(
                        padding: const EdgeInsets.all(24),
                        child: Text(
                          macros.isEmpty
                              ? 'No saved replies yet. You can write one under '
                                    'More, Organization settings.'
                              : 'No saved reply matches that search',
                          style: AppTypography.body.copyWith(
                            color: AppColors.textSecondary,
                          ),
                          textAlign: TextAlign.center,
                        ),
                      ),
                    );
                  }
                  return ListView.builder(
                    itemCount: rows.length,
                    itemBuilder: (context, i) => _PickerRow(
                      macro: rows[i],
                      busy: _applyingId != null,
                      applying: _applyingId == rows[i].id,
                      onTap: () => _pick(rows[i]),
                    ),
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _PickerRow extends StatelessWidget {
  const _PickerRow({
    required this.macro,
    required this.busy,
    required this.applying,
    required this.onTap,
  });

  final Macro macro;
  final bool busy;
  final bool applying;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final chips = macroActionChips(macro);
    return InkWell(
      onTap: busy ? null : onTap,
      child: Container(
        decoration: BoxDecoration(
          border: Border(bottom: BorderSide(color: AppColors.gray100)),
        ),
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    macro.title,
                    style: AppTypography.body.copyWith(
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  if (macro.hasBody) ...[
                    const SizedBox(height: 2),
                    Text(
                      macro.body,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textSecondary,
                      ),
                    ),
                  ],
                  if (chips.isNotEmpty) ...[
                    const SizedBox(height: 2),
                    // What it does to the ticket, so an agent is not surprised
                    // by a status change they did not read about.
                    Text(
                      chips.map((c) => c.label).join(' · '),
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textSecondary,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ],
                  const SizedBox(height: 6),
                  StatusBadge(
                    label: macro.scopeLabel,
                    color: macro.isPersonal
                        ? AppColors.gray500
                        : AppColors.primary600,
                  ),
                ],
              ),
            ),
            const SizedBox(width: 12),
            if (applying)
              const SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            else if (!macro.hasBody)
              // No text to insert, so picking it applies it now; the button
              // says so rather than letting a tap look like an insert.
              SizedBox(
                height: 44,
                child: FilledButton.tonal(
                  onPressed: busy ? null : onTap,
                  child: const Text('Apply'),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
