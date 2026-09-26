import 'package:bottle_crm/data/models/auth_response.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// `isOrgAdminProvider` is the app's one admin rule, and it reads the
/// server's `is_organization_admin` fact, never `role`.
///
/// The API computes that fact with `is_org_admin`: the ADMIN role, or a
/// Django superuser's membership. Gating on `role == 'ADMIN'` showed a
/// USER-role superuser read-only settings screens the API would have let
/// them change.
bool adminFor(Organization? org) {
  final container = ProviderContainer(
    overrides: [selectedOrgProvider.overrideWithValue(org)],
  );
  addTearDown(container.dispose);
  return container.read(isOrgAdminProvider);
}

Organization parse(Map<String, dynamic> extra) =>
    Organization.fromJson({'id': 'org-1', 'name': 'Acme', ...extra});

void main() {
  group('isOrgAdminProvider', () {
    test('an ADMIN is an admin', () {
      expect(
        adminFor(parse({'role': 'ADMIN', 'is_organization_admin': true})),
        isTrue,
      );
    });

    test('a USER-role superuser is an admin, because the fact says so', () {
      expect(
        adminFor(parse({'role': 'USER', 'is_organization_admin': true})),
        isTrue,
      );
    });

    test('a plain member is not', () {
      expect(
        adminFor(parse({'role': 'USER', 'is_organization_admin': false})),
        isFalse,
      );
    });

    test('the fact wins over the role when both are present', () {
      expect(
        adminFor(parse({'role': 'ADMIN', 'is_organization_admin': false})),
        isFalse,
      );
    });

    test('no selected org is not an admin', () {
      expect(adminFor(null), isFalse);
    });
  });

  group('Organization.fromJson', () {
    test('only a literal true counts', () {
      for (final value in ['true', 1, null, 'yes']) {
        expect(
          parse({
            'role': 'ADMIN',
            'is_organization_admin': value,
          }).isOrganizationAdmin,
          isFalse,
          reason: 'is_organization_admin: $value',
        );
      }
    });

    test('an org cached before 1.11.0 (no fact) falls back to its role', () {
      expect(parse({'role': 'ADMIN'}).isOrganizationAdmin, isTrue);
      expect(parse({'role': 'USER'}).isOrganizationAdmin, isFalse);
      expect(parse({}).isOrganizationAdmin, isFalse);
    });

    test('the fact survives the storage round trip', () {
      final org = parse({'role': 'USER', 'is_organization_admin': true});
      expect(Organization.fromJson(org.toJson()).isOrganizationAdmin, isTrue);
    });
  });

  group('Organization.withMembership (the switch response refreshes it)', () {
    final stale = parse({
      'role': 'USER',
      'is_organization_admin': false,
      'default_currency': 'EUR',
    });

    test('a promotion since sign-in reaches the admin gate', () {
      final fresh = stale.withMembership({
        'id': 'p-1',
        'role': 'ADMIN',
        'is_organization_admin': true,
      });
      expect(fresh.isOrganizationAdmin, isTrue);
      expect(fresh.role, 'ADMIN');
      expect(adminFor(fresh), isTrue);
      expect(fresh.defaultCurrency, 'EUR', reason: 'org facts are kept');
    });

    test('a demotion since sign-in removes it', () {
      final admin = parse({'role': 'ADMIN', 'is_organization_admin': true});
      final fresh = admin.withMembership({
        'role': 'USER',
        'is_organization_admin': false,
      });
      expect(adminFor(fresh), isFalse);
    });

    test('an org built from the create response is admin at once', () {
      final created = parse({'role': 'ADMIN', 'is_organization_admin': true});
      expect(adminFor(created), isTrue);
    });

    test('an absent or malformed profile keeps what was cached', () {
      final admin = parse({'role': 'ADMIN', 'is_organization_admin': true});
      expect(admin.withMembership(null).isOrganizationAdmin, isTrue);
      expect(admin.withMembership('x').isOrganizationAdmin, isTrue);
      expect(
        admin.withMembership({
          'is_organization_admin': 'false',
        }).isOrganizationAdmin,
        isTrue,
      );
    });
  });
}
