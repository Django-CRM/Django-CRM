import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/contact.dart';
import 'package:bottle_crm/data/models/lookup_models.dart';
import 'package:bottle_crm/providers/contacts_provider.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/screens/contacts/contact_form_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

/// The contact's linked account stays on the picker and in the save.
///
/// The accounts lookup carries only the accounts the user can open, so a
/// contact an admin linked to some other company has an `account` the picker
/// does not list. The picker used to read "No account" for it. The save still
/// sent the id, which the server keeps as an unchanged link (D39), so the
/// screen said one thing and the record another.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const visible = AccountLookup(id: 'a-visible', name: 'Visible Co');

  Contact contact({String? accountId, String? accountName}) =>
      Contact.fromJson({
        'id': 'c1',
        'first_name': 'Ada',
        'last_name': 'Lovelace',
        'account': accountId,
        'account_detail': accountId == null
            ? null
            : {'id': accountId, 'name': accountName},
      });

  Future<_FakeContacts> open(WidgetTester tester, Contact stored) async {
    final fake = _FakeContacts(stored);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          contactsProvider.overrideWith(() => fake),
          accountOptionsProvider.overrideWithValue(const [visible]),
        ],
        child: MaterialApp.router(
          theme: AppTheme.light,
          routerConfig: GoRouter(
            initialLocation: '/contacts/edit',
            routes: [
              GoRoute(
                path: '/contacts',
                builder: (_, _) => const Text('list'),
                routes: [
                  GoRoute(
                    path: 'edit',
                    builder: (_, _) => const ContactFormScreen(contactId: 'c1'),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    return fake;
  }

  Future<void> save(WidgetTester tester) async {
    final button = find.text('Save changes');
    await tester.ensureVisible(button);
    await tester.tap(button);
    await tester.pumpAndSettle();
  }

  testWidgets('shows and keeps a linked account the lookup does not carry', (
    tester,
  ) async {
    final fake = await open(
      tester,
      contact(accountId: 'a-hidden', accountName: 'Hidden Holdings'),
    );
    expect(find.text('Hidden Holdings'), findsOneWidget);
    expect(find.text('No account'), findsNothing);

    await save(tester);
    expect(fake.updates.single['account'], 'a-hidden');
  });

  testWidgets('a linked account the lookup carries is listed once', (
    tester,
  ) async {
    final fake = await open(
      tester,
      contact(accountId: 'a-visible', accountName: 'Visible Co'),
    );
    expect(find.text('Visible Co'), findsOneWidget);

    await save(tester);
    expect(fake.updates.single['account'], 'a-visible');
  });

  testWidgets('no linked account reads as No account and sends none', (
    tester,
  ) async {
    final fake = await open(tester, contact());
    expect(find.text('No account'), findsOneWidget);

    await save(tester);
    expect(fake.updates.single['account'], isNull);
  });
}

class _FakeContacts extends ContactsNotifier {
  _FakeContacts(this.stored);

  final Contact stored;

  /// Every update body, in order.
  final List<Map<String, dynamic>> updates = [];

  @override
  Future<ContactsListData> build() async => const ContactsListData();

  @override
  Future<Contact?> getContact(String id) async => stored;

  @override
  Future<String?> updateContact(String id, Map<String, dynamic> payload) async {
    updates.add(payload);
    return null;
  }
}
