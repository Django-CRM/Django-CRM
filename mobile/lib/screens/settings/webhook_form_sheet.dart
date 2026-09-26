import 'package:flutter/material.dart';

import '../../core/theme/theme.dart';
import '../../data/models/webhook.dart';
import '../../providers/webhooks_provider.dart';

/// Create or edit a webhook. Returns the draft, or null if dismissed.
///
/// The two checks here (an https URL, at least one event) are hints so the
/// sheet does not submit something certain to bounce. The serializer is the
/// rule: it also resolves the host and refuses private addresses, which this
/// app cannot judge.
Future<WebhookDraft?> showWebhookFormSheet(
  BuildContext context, {
  required List<WebhookEventGroup> catalogue,
  WebhookEndpoint? initial,
}) {
  return showModalBottomSheet<WebhookDraft>(
    context: context,
    isScrollControlled: true,
    builder: (context) =>
        WebhookFormSheet(catalogue: catalogue, initial: initial),
  );
}

class WebhookFormSheet extends StatefulWidget {
  const WebhookFormSheet({super.key, required this.catalogue, this.initial});

  final List<WebhookEventGroup> catalogue;
  final WebhookEndpoint? initial;

  @override
  State<WebhookFormSheet> createState() => _WebhookFormSheetState();
}

class _WebhookFormSheetState extends State<WebhookFormSheet> {
  late final TextEditingController _url = TextEditingController(
    text: widget.initial?.url ?? '',
  );
  late final TextEditingController _description = TextEditingController(
    text: widget.initial?.description ?? '',
  );
  late String _format = widget.initial?.format ?? WebhookEndpoint.formatJson;
  late final Set<String> _events = {...?widget.initial?.events};
  String? _error;

  @override
  void dispose() {
    _url.dispose();
    _description.dispose();
    super.dispose();
  }

  void _submit() {
    final url = _url.text.trim();
    if (!url.toLowerCase().startsWith('https://')) {
      setState(() => _error = 'The URL must start with https://.');
      return;
    }
    if (_events.isEmpty) {
      setState(() => _error = 'Choose at least one event.');
      return;
    }
    // Catalogue order, so the saved list reads the same on both clients.
    final ordered = [
      for (final group in widget.catalogue)
        for (final name in group.events)
          if (_events.contains(name)) name,
    ];
    Navigator.of(context).pop((
      url: url,
      description: _description.text.trim(),
      format: _format,
      events: ordered,
    ));
  }

  @override
  Widget build(BuildContext context) {
    final editing = widget.initial != null;
    return Padding(
      padding: EdgeInsets.only(
        bottom: MediaQuery.of(context).viewInsets.bottom,
      ),
      child: DraggableScrollableSheet(
        expand: false,
        initialChildSize: 0.9,
        maxChildSize: 0.95,
        builder: (context, controller) => ListView(
          controller: controller,
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
          children: [
            Text(
              editing ? 'Edit webhook' : 'New webhook',
              style: AppTypography.h3.copyWith(fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 14),
            TextField(
              controller: _url,
              keyboardType: TextInputType.url,
              autocorrect: false,
              enableSuggestions: false,
              maxLength: 500,
              decoration: const InputDecoration(
                labelText: 'Endpoint URL',
                hintText: 'https://hooks.zapier.com/hooks/catch/...',
                helperText: 'HTTPS on the standard port, reachable publicly',
                helperMaxLines: 2,
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 4),
            TextField(
              controller: _description,
              maxLength: 255,
              textCapitalization: TextCapitalization.sentences,
              decoration: const InputDecoration(
                labelText: 'What is it for?',
                hintText: 'e.g. Zapier: new leads to the sales sheet',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 4),
            DropdownButtonFormField<String>(
              initialValue: _format,
              isExpanded: true,
              decoration: const InputDecoration(
                labelText: 'Send as',
                border: OutlineInputBorder(),
              ),
              items: const [
                DropdownMenuItem(
                  value: WebhookEndpoint.formatJson,
                  child: Text('Signed JSON (Zapier, n8n, your code)'),
                ),
                DropdownMenuItem(
                  value: WebhookEndpoint.formatSlack,
                  child: Text('Slack message'),
                ),
              ],
              onChanged: (v) => setState(() => _format = v ?? _format),
            ),
            const SizedBox(height: 16),
            Text(
              'EVENTS',
              style: AppTypography.overline.copyWith(
                color: AppColors.textSecondary,
                letterSpacing: 1.2,
              ),
            ),
            for (final group in widget.catalogue) ...[
              Padding(
                padding: const EdgeInsets.only(top: 12, bottom: 2),
                child: Text(
                  group.label,
                  style: AppTypography.body.copyWith(
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              for (final name in group.events)
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  controlAffinity: ListTileControlAffinity.leading,
                  title: Text(webhookEventLabel(name)),
                  value: _events.contains(name),
                  onChanged: (checked) => setState(() {
                    if (checked ?? false) {
                      _events.add(name);
                    } else {
                      _events.remove(name);
                    }
                  }),
                ),
            ],
            if (_error != null) ...[
              const SizedBox(height: 8),
              Text(
                _error!,
                style: AppTypography.caption.copyWith(
                  color: AppColors.danger600,
                ),
              ),
            ],
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: SizedBox(
                    height: 48,
                    child: OutlinedButton(
                      onPressed: () => Navigator.of(context).pop(),
                      child: const Text('Cancel'),
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: SizedBox(
                    height: 48,
                    child: FilledButton(
                      onPressed: _submit,
                      child: Text(editing ? 'Save' : 'Add webhook'),
                    ),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
