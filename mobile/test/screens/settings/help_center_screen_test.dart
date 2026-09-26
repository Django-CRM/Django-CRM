import 'package:bottle_crm/data/models/help_center_settings.dart';
import 'package:bottle_crm/providers/help_center_provider.dart';
import 'package:bottle_crm/screens/settings/help_center_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// The public help center switch on the phone.
///
/// Rendered at 390px and at 1.3x text, because a Row that only just fits at
/// 1.0 is where a large-text phone overflows, and Flutter reports an overflow
/// as a thrown error that `takeException` returns. Beyond layout: the form
/// appears only when the API says `can_edit`, and a refused save shows the
/// API's sentence under the field rather than in a snackbar that vanishes.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  void useViewport(
    WidgetTester tester, {
    required Size size,
    double textScale = 1.0,
  }) {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = Size(size.width * 3, size.height * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
  }

  const phone = Size(390, 844);
  const tablet = Size(834, 1112);

  const live = HelpCenterSettings(
    enabled: true,
    slug: 'acme-customer-support-and-onboarding-guides',
    publicUrl:
        'https://app.example.com/help-center/'
        'acme-customer-support-and-onboarding-guides',
    canEdit: true,
  );
  const memberOff = HelpCenterSettings();

  Future<void> pump(
    WidgetTester tester,
    HelpCenterSettings settings, {
    Size size = phone,
    double textScale = 1.0,
    String? saveError,
  }) async {
    useViewport(tester, size: size, textScale: textScale);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          helpCenterSettingsProvider.overrideWith(
            () => _FakeHelpCenter(settings, saveError),
          ),
        ],
        child: const MaterialApp(home: HelpCenterScreen()),
      ),
    );
    await tester.pumpAndSettle();
  }

  for (final scale in [1.0, 1.3]) {
    testWidgets('admin view fits a 390px phone at ${scale}x text', (
      tester,
    ) async {
      await pump(tester, live, textScale: scale);
      expect(tester.takeException(), isNull);
      expect(find.text('Open the public page'), findsOneWidget);
      await tester.scrollUntilVisible(
        find.text('Save'),
        200,
        // The page's list, not the address field's own scrollable.
        scrollable: find.byType(Scrollable).first,
      );
      expect(tester.takeException(), isNull);
    });

    testWidgets('member view fits a 390px phone at ${scale}x text', (
      tester,
    ) async {
      await pump(tester, memberOff, textScale: scale);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('holds up at tablet width', (tester) async {
    await pump(tester, live, size: tablet, textScale: 1.3);
    expect(tester.takeException(), isNull);
  });

  testWidgets('a member sees the setting but no form', (tester) async {
    await pump(tester, memberOff);
    expect(find.text('Off'), findsOneWidget);
    expect(find.text('Not chosen yet'), findsOneWidget);
    expect(find.text('Save'), findsNothing);
    expect(find.byType(TextField), findsNothing);
    // Nothing to open while it is off.
    expect(find.text('Open the public page'), findsNothing);
  });

  testWidgets('an admin sees the form', (tester) async {
    await pump(tester, live);
    expect(find.byType(TextField), findsOneWidget);
    expect(find.byType(SwitchListTile), findsOneWidget);
  });

  testWidgets('a refused save shows the API sentence under the field', (
    tester,
  ) async {
    await pump(
      tester,
      live,
      saveError: 'That address is already taken. Choose another.',
    );
    await tester.scrollUntilVisible(
      find.text('Save'),
      200,
      // The page's list, not the address field's own scrollable.
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    expect(
      find.text('That address is already taken. Choose another.'),
      findsOneWidget,
    );
  });

  group('HelpCenterSettings.fromJson', () {
    test('reads the API payload', () {
      final s = HelpCenterSettings.fromJson(const {
        'help_center_enabled': true,
        'help_center_slug': 'acme',
        'public_url': 'https://app.example.com/help-center/acme',
        'can_edit': false,
      });
      expect(s.enabled, isTrue);
      expect(s.slug, 'acme');
      expect(s.isLive, isTrue);
      expect(s.canEdit, isFalse);
    });

    test('an enabled flag without an address is not live', () {
      final s = HelpCenterSettings.fromJson(const {
        'help_center_enabled': true,
        'help_center_slug': null,
        'public_url': null,
      });
      expect(s.isLive, isFalse);
      expect(s.canEdit, isFalse);
    });
  });

  group('saveErrorText', () {
    test('drops the field label the API client adds', () {
      expect(
        saveErrorText('Help center slug: \'admin\' is reserved.'),
        '\'admin\' is reserved.',
      );
    });

    test('falls back when there is nothing to say', () {
      expect(saveErrorText(null), 'Could not save the help center.');
    });
  });
}

class _FakeHelpCenter extends HelpCenterSettingsNotifier {
  _FakeHelpCenter(this._settings, this._saveError);

  final HelpCenterSettings _settings;
  final String? _saveError;

  @override
  Future<HelpCenterSettings> build() async => _settings;

  @override
  Future<String?> save({required bool enabled, required String slug}) async =>
      _saveError;
}
