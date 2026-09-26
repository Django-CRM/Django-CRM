import 'package:bottle_crm/data/models/escalation_policy.dart';
import 'package:bottle_crm/data/models/lookup_models.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/screens/settings/escalation_policy_form_sheet.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// A profile as `ProfileSerializer` sends it.
Map<String, dynamic> person(
  String id,
  String name, {
  bool isActive = true,
  String email = 'someone@example.com',
}) => {
  'id': id,
  'is_active': isActive,
  'user_details': {'id': 'u-$id', 'name': name, 'email': email},
};

Map<String, dynamic> policyJson({
  String id = 'e1',
  String priority = 'Urgent',
  bool isActive = true,
  String firstResponseAction = escalationActionNotify,
  String resolutionAction = escalationActionNotify,
  Map<String, dynamic>? firstResponseTarget,
  Map<String, dynamic>? resolutionTarget,
  Map<String, dynamic>? notifyTeam,
  int firstResponseBreaches = 0,
  int resolutionBreaches = 0,
}) => {
  'id': id,
  'priority': priority,
  'is_active': isActive,
  'first_response_action': firstResponseAction,
  'resolution_action': resolutionAction,
  'first_response_target': firstResponseTarget,
  'resolution_target': resolutionTarget,
  'notify_team': notifyTeam,
  'breaches_last_30d': {
    'first_response': firstResponseBreaches,
    'resolution': resolutionBreaches,
  },
};

final alice = person('p1', 'Alice');
final support = {'id': 't1', 'name': 'Support'};
final supportTeam = {'id': 't2', 'name': 'Support Team'};

EscalationPolicy build({
  bool isActive = true,
  String firstResponseAction = escalationActionNotify,
  String resolutionAction = escalationActionNotify,
  Map<String, dynamic>? firstResponseTarget,
  Map<String, dynamic>? resolutionTarget,
  Map<String, dynamic>? notifyTeam,
  int firstResponseBreaches = 0,
  int resolutionBreaches = 0,
}) => EscalationPolicy.fromJson(
  policyJson(
    isActive: isActive,
    firstResponseAction: firstResponseAction,
    resolutionAction: resolutionAction,
    firstResponseTarget: firstResponseTarget ?? alice,
    resolutionTarget: resolutionTarget ?? alice,
    notifyTeam: notifyTeam,
    firstResponseBreaches: firstResponseBreaches,
    resolutionBreaches: resolutionBreaches,
  ),
);

void main() {
  group('EscalationPolicy.fromJson', () {
    test('flattens both targets and the team', () {
      final policy = EscalationPolicy.fromJson(
        policyJson(
          firstResponseTarget: alice,
          resolutionTarget: person('p2', 'Brin'),
          notifyTeam: support,
        ),
      );
      expect(policy.firstResponseTarget!.displayName, 'Alice');
      expect(policy.resolutionTarget!.displayName, 'Brin');
      expect(policy.notifyTeam!.name, 'Support');
    });

    test('leaves an unset target null rather than inventing one', () {
      final policy = EscalationPolicy.fromJson(policyJson());
      expect(policy.firstResponseTarget, isNull);
      expect(policy.resolutionTarget, isNull);
      expect(policy.notifyTeam, isNull);
    });

    test('falls back to the email local part when a user has no name', () {
      final policy = EscalationPolicy.fromJson(
        policyJson(
          firstResponseTarget: person('p3', '', email: 'dana@example.com'),
        ),
      );
      expect(policy.firstResponseTarget!.displayName, 'dana');
    });

    test('reads the breach counts per half', () {
      final policy = build(firstResponseBreaches: 11, resolutionBreaches: 4);
      expect(policy.breachesFor(EscalationHalf.firstResponse), 11);
      expect(policy.breachesFor(EscalationHalf.resolution), 4);
    });

    test('a missing breaches block reads as zero, not as a crash', () {
      final json = policyJson()..remove('breaches_last_30d');
      final policy = EscalationPolicy.fromJson(json);
      expect(policy.breachesFor(EscalationHalf.firstResponse), 0);
    });
  });

  group('escalationActionNotifies', () {
    test('is true only for the two actions that build a recipient list', () {
      expect(escalationActionNotifies(escalationActionNotify), isTrue);
      expect(
        escalationActionNotifies(escalationActionNotifyAndReassign),
        isTrue,
      );
      expect(escalationActionNotifies(escalationActionReassign), isFalse);
    });
  });

  group('firesFor', () {
    test('is false for every half of a policy that is turned off', () {
      final policy = build(isActive: false, notifyTeam: support);
      expect(policy.firesFor(EscalationHalf.firstResponse), isFalse);
      expect(policy.firesFor(EscalationHalf.resolution), isFalse);
    });

    // The three combinations the web reported as live while the engine did
    // nothing with them, one test each.
    test('is false for notify with a team but no target', () {
      final policy = EscalationPolicy.fromJson(
        policyJson(resolutionTarget: alice, notifyTeam: support),
      );
      expect(policy.firesFor(EscalationHalf.firstResponse), isFalse);
    });

    test('is false for notify_and_reassign with no target and no team', () {
      final policy = EscalationPolicy.fromJson(
        policyJson(
          firstResponseAction: escalationActionNotifyAndReassign,
          resolutionTarget: alice,
        ),
      );
      expect(policy.firesFor(EscalationHalf.firstResponse), isFalse);
    });

    test('is false for notify_and_reassign with a team but no target', () {
      final policy = EscalationPolicy.fromJson(
        policyJson(
          firstResponseAction: escalationActionNotifyAndReassign,
          resolutionTarget: alice,
          notifyTeam: support,
        ),
      );
      expect(policy.firesFor(EscalationHalf.firstResponse), isFalse);
    });

    test('is false for reassign with no target', () {
      final policy = EscalationPolicy.fromJson(
        policyJson(
          firstResponseAction: escalationActionReassign,
          resolutionTarget: alice,
        ),
      );
      expect(policy.firesFor(EscalationHalf.firstResponse), isFalse);
    });

    test('is true for every action once a target is set', () {
      for (final action in escalationActionLabels.keys) {
        expect(
          build(
            firstResponseAction: action,
          ).firesFor(EscalationHalf.firstResponse),
          isTrue,
          reason: action,
        );
      }
    });

    test('reads the half it was asked about, not the other one', () {
      final policy = EscalationPolicy.fromJson(
        policyJson(resolutionTarget: alice),
      );
      expect(policy.firesFor(EscalationHalf.firstResponse), isFalse);
      expect(policy.firesFor(EscalationHalf.resolution), isTrue);
    });
  });

  group('notifiesTeamFor', () {
    test('is true when a notifying half has a team', () {
      final policy = build(notifyTeam: support);
      expect(policy.notifiesTeamFor(EscalationHalf.firstResponse), isTrue);
    });

    test('is false on a reassign half, which sends no mail', () {
      final policy = build(
        firstResponseAction: escalationActionReassign,
        notifyTeam: support,
      );
      expect(policy.notifiesTeamFor(EscalationHalf.firstResponse), isFalse);
    });

    test('is false when the half cannot fire at all', () {
      final policy = EscalationPolicy.fromJson(
        policyJson(resolutionTarget: alice, notifyTeam: support),
      );
      expect(policy.notifiesTeamFor(EscalationHalf.firstResponse), isFalse);
    });
  });

  group('escalationOutcomeSentence', () {
    test('names the policy being off before anything else', () {
      final policy = build(isActive: false, notifyTeam: support);
      expect(
        escalationOutcomeSentence(policy, EscalationHalf.firstResponse),
        'Nothing. The policy is turned off.',
      );
    });

    test('says nothing happens with no target and no team', () {
      final policy = EscalationPolicy.fromJson(
        policyJson(resolutionTarget: alice),
      );
      expect(
        escalationOutcomeSentence(policy, EscalationHalf.firstResponse),
        'Nothing. No target is set.',
      );
    });

    test(
      'names the team when one is set with no target, which is the trap',
      () {
        final policy = EscalationPolicy.fromJson(
          policyJson(resolutionTarget: alice, notifyTeam: support),
        );
        expect(
          escalationOutcomeSentence(policy, EscalationHalf.firstResponse),
          'Nothing. No target is set, and the Support team is not notified on '
          'its own.',
        );
      },
    );

    test('appends the team on a notifying half', () {
      final policy = build(notifyTeam: support);
      expect(
        escalationOutcomeSentence(policy, EscalationHalf.firstResponse),
        'Notify Alice and the Support team.',
      );
    });

    test('does not say "team" twice for a team named Support Team', () {
      final policy = build(notifyTeam: supportTeam);
      expect(
        escalationOutcomeSentence(policy, EscalationHalf.firstResponse),
        'Notify Alice and the Support Team.',
      );
    });

    test('leaves the team out of a reassign half', () {
      final policy = build(
        firstResponseAction: escalationActionReassign,
        notifyTeam: support,
      );
      expect(
        escalationOutcomeSentence(policy, EscalationHalf.firstResponse),
        'Reassign to Alice.',
      );
    });

    test('never renders a label with nothing after it', () {
      for (final action in escalationActionLabels.keys) {
        for (final team in [null, support]) {
          final policy = EscalationPolicy.fromJson(
            policyJson(
              firstResponseAction: action,
              resolutionTarget: alice,
              notifyTeam: team,
            ),
          );
          final text = escalationOutcomeSentence(
            policy,
            EscalationHalf.firstResponse,
          );
          expect(text.endsWith(' .'), isFalse, reason: '$action/$team');
          expect(text, startsWith('Nothing.'));
        }
      }
    });
  });

  group('escalationTeamIgnoredNote', () {
    test('warns when a team is set on a reassign half', () {
      final policy = build(
        firstResponseAction: escalationActionReassign,
        notifyTeam: support,
      );
      expect(
        escalationTeamIgnoredNote(policy, EscalationHalf.firstResponse),
        'The Support team is not notified here: this half only reassigns.',
      );
    });

    test('is null when the half notifies', () {
      final policy = build(
        firstResponseAction: escalationActionNotifyAndReassign,
        notifyTeam: support,
      );
      expect(
        escalationTeamIgnoredNote(policy, EscalationHalf.firstResponse),
        isNull,
      );
    });

    test('is null with no team', () {
      final policy = build(firstResponseAction: escalationActionReassign);
      expect(
        escalationTeamIgnoredNote(policy, EscalationHalf.firstResponse),
        isNull,
      );
    });

    test('is null on a half that does not fire', () {
      final policy = EscalationPolicy.fromJson(
        policyJson(
          firstResponseAction: escalationActionReassign,
          resolutionTarget: alice,
          notifyTeam: support,
        ),
      );
      expect(
        escalationTeamIgnoredNote(policy, EscalationHalf.firstResponse),
        isNull,
      );
    });
  });

  group('isDead and breachesGoingNowhere', () {
    test('a policy dead on one half only is not dead', () {
      final policy = EscalationPolicy.fromJson(
        policyJson(resolutionTarget: alice),
      );
      expect(policy.isDead, isFalse);
    });

    test('a policy with neither target set is dead', () {
      expect(EscalationPolicy.fromJson(policyJson()).isDead, isTrue);
    });

    test('counts only the breaches on halves that cannot fire', () {
      final policy = EscalationPolicy.fromJson(
        policyJson(
          resolutionTarget: alice,
          firstResponseBreaches: 11,
          resolutionBreaches: 4,
        ),
      );
      expect(policy.breachesGoingNowhere, 11);
    });

    test('counts both halves when the policy is off', () {
      final policy = build(
        isActive: false,
        firstResponseBreaches: 11,
        resolutionBreaches: 4,
      );
      expect(policy.breachesGoingNowhere, 15);
    });

    test('counts a team-but-no-target half', () {
      final policy = EscalationPolicy.fromJson(
        policyJson(
          resolutionTarget: alice,
          notifyTeam: support,
          firstResponseBreaches: 7,
        ),
      );
      expect(policy.breachesGoingNowhere, 7);
    });

    test('is zero when both halves fire', () {
      final policy = build(firstResponseBreaches: 9, resolutionBreaches: 9);
      expect(policy.breachesGoingNowhere, 0);
    });
  });

  group('deactivatedTargets', () {
    test('lists a target whose account is turned off', () {
      final policy = EscalationPolicy.fromJson(
        policyJson(
          firstResponseTarget: person('p9', 'Cai', isActive: false),
          resolutionTarget: alice,
        ),
      );
      expect(policy.deactivatedTargets.map((t) => t.displayName), ['Cai']);
    });

    test('is empty when every target is active', () {
      expect(build().deactivatedTargets, isEmpty);
    });
  });

  group('sortedEscalationPolicies', () {
    test('sorts by severity, not alphabetically', () {
      final policies = [
        EscalationPolicy.fromJson(policyJson(id: 'a', priority: 'High')),
        EscalationPolicy.fromJson(policyJson(id: 'b', priority: 'Low')),
        EscalationPolicy.fromJson(policyJson(id: 'c', priority: 'Urgent')),
        EscalationPolicy.fromJson(policyJson(id: 'd', priority: 'Normal')),
      ];
      expect(sortedEscalationPolicies(policies).map((p) => p.priority), [
        'Urgent',
        'High',
        'Normal',
        'Low',
      ]);
    });

    test('does not mutate the list it was given', () {
      final policies = [
        EscalationPolicy.fromJson(policyJson(id: 'a', priority: 'Low')),
        EscalationPolicy.fromJson(policyJson(id: 'b', priority: 'Urgent')),
      ];
      sortedEscalationPolicies(policies);
      expect(policies.first.priority, 'Low');
    });
  });

  group('unconfiguredEscalationPriorities', () {
    test('returns the priorities with no policy, worst first', () {
      final policies = [
        EscalationPolicy.fromJson(policyJson(priority: 'High')),
      ];
      expect(unconfiguredEscalationPriorities(policies), [
        'Urgent',
        'Normal',
        'Low',
      ]);
    });

    test('is empty once all four are configured', () {
      final policies = [
        for (final priority in escalationPriorities)
          EscalationPolicy.fromJson(policyJson(priority: priority)),
      ];
      expect(unconfiguredEscalationPriorities(policies), isEmpty);
    });
  });

  group('teamPhrase', () {
    test('adds the word so a bare name reads as a team', () {
      expect(teamPhrase('Support'), 'the Support team');
    });

    test('does not repeat it when the name already says team', () {
      expect(teamPhrase('Support Team'), 'the Support Team');
      expect(teamPhrase('support teams'), 'the support teams');
      expect(teamPhrase('Team'), 'the Team');
    });

    test('does not fire on a name that merely ends in those letters', () {
      expect(teamPhrase('Downsteam'), 'the Downsteam team');
    });
  });

  group('joinWithAnd', () {
    test('joins none, one, two and three parts', () {
      expect(joinWithAnd([]), '');
      expect(joinWithAnd(['Urgent']), 'Urgent');
      expect(joinWithAnd(['Urgent', 'High']), 'Urgent and High');
      expect(joinWithAnd(['Urgent', 'High', 'Low']), 'Urgent, High and Low');
    });
  });

  group('payloads', () {
    test('a create carries the priority and the starting state', () {
      final body = escalationCreatePayload(
        priority: 'Urgent',
        firstResponseAction: escalationActionNotify,
        resolutionAction: escalationActionReassign,
        firstResponseTargetId: 'p1',
        resolutionTargetId: 'p2',
        notifyTeamId: 't1',
      );
      expect(body['priority'], 'Urgent');
      expect(body['is_active'], isTrue);
      expect(body['first_response_target_id'], 'p1');
      expect(body['resolution_action'], escalationActionReassign);
      expect(body['notify_team_id'], 't1');
    });

    test('an empty picker becomes null, which is how the API clears an FK', () {
      final body = escalationCreatePayload(
        priority: 'Low',
        firstResponseAction: escalationActionNotify,
        resolutionAction: escalationActionNotify,
        firstResponseTargetId: '',
        resolutionTargetId: '   ',
        notifyTeamId: null,
      );
      expect(body['first_response_target_id'], isNull);
      expect(body['resolution_target_id'], isNull);
      expect(body['notify_team_id'], isNull);
    });

    test('an edit omits priority, which the view strips anyway', () {
      final body = escalationUpdatePayload(
        firstResponseAction: escalationActionNotify,
        resolutionAction: escalationActionNotify,
        firstResponseTargetId: 'p1',
        resolutionTargetId: 'p1',
        notifyTeamId: null,
      );
      expect(body.containsKey('priority'), isFalse);
    });

    test('an edit omits is_active, which the row controls own', () {
      final body = escalationUpdatePayload(
        firstResponseAction: escalationActionNotify,
        resolutionAction: escalationActionNotify,
        firstResponseTargetId: 'p1',
        resolutionTargetId: 'p1',
        notifyTeamId: null,
      );
      expect(body.containsKey('is_active'), isFalse);
    });

    test('turning a policy on sends only is_active', () {
      expect(escalationActivePayload(true), {'is_active': true});
      expect(escalationActivePayload(false), {'is_active': false});
    });
  });

  group('SLA targets on the policy', () {
    test('reads the hours an org configured', () {
      final policy = EscalationPolicy.fromJson({
        ...policyJson(),
        'first_response_hours': 2,
        'resolution_hours': 8,
      });
      expect(policy.firstResponseHours, 2);
      expect(policy.resolutionHours, 8);
    });

    test(
      'leaves an unconfigured target null rather than guessing a default',
      () {
        // The default belongs to the backend. Inventing one here would print a
        // number the server might not agree with.
        final policy = EscalationPolicy.fromJson(policyJson());
        expect(policy.firstResponseHours, isNull);
        expect(policy.resolutionHours, isNull);
      },
    );

    test('names the built-in default when the org set no target', () {
      final policy = EscalationPolicy.fromJson(policyJson(priority: 'Urgent'));
      expect(policy.firstResponseTargetLabel, '1h default');
      expect(policy.resolutionTargetLabel, '4h default');
    });

    test('names the org target plainly when one is set', () {
      final policy = EscalationPolicy.fromJson({
        ...policyJson(priority: 'Urgent'),
        'first_response_hours': 6,
      });
      expect(policy.firstResponseTargetLabel, '6h');
      expect(policy.resolutionTargetLabel, '4h default');
    });

    test('sends the hours on create', () {
      final body = escalationCreatePayload(
        priority: 'Urgent',
        firstResponseAction: escalationActionNotify,
        resolutionAction: escalationActionNotify,
        firstResponseTargetId: 'p1',
        resolutionTargetId: 'p1',
        notifyTeamId: null,
        firstResponseHours: 3,
        resolutionHours: 12,
      );
      expect(body['first_response_hours'], 3);
      expect(body['resolution_hours'], 12);
    });

    test('sends null, not 0, when a target is left blank', () {
      // 0 would put the deadline on created_at, so every new case would open
      // already breached. The serializer rejects it; this never sends it.
      final body = escalationCreatePayload(
        priority: 'Urgent',
        firstResponseAction: escalationActionNotify,
        resolutionAction: escalationActionNotify,
        firstResponseTargetId: 'p1',
        resolutionTargetId: 'p1',
        notifyTeamId: null,
      );
      expect(body['first_response_hours'], isNull);
      expect(body['resolution_hours'], isNull);
    });

    test('sends the hours on update', () {
      final body = escalationUpdatePayload(
        firstResponseAction: escalationActionNotify,
        resolutionAction: escalationActionNotify,
        firstResponseTargetId: 'p1',
        resolutionTargetId: 'p1',
        notifyTeamId: null,
        firstResponseHours: null,
        resolutionHours: 18,
      );
      expect(body.containsKey('first_response_hours'), isTrue);
      expect(body['first_response_hours'], isNull);
      expect(body['resolution_hours'], 18);
    });
  });

  group('next response target', () {
    test('reads the hours an org configured', () {
      final policy = EscalationPolicy.fromJson({
        ...policyJson(),
        'next_response_hours': 3,
      });
      expect(policy.nextResponseHours, 3);
      expect(policy.nextResponseTargetLabel, '3h');
    });

    test('leaves it null when unset, and names the built-in default', () {
      final policy = EscalationPolicy.fromJson(policyJson(priority: 'Low'));
      expect(policy.nextResponseHours, isNull);
      expect(policy.nextResponseTargetLabel, '24h default');
    });

    test('mirrors DEFAULT_NEXT_RESPONSE_SLA, Normal for an unknown one', () {
      expect(defaultNextResponseHours('Urgent'), 1);
      expect(defaultNextResponseHours('High'), 4);
      expect(defaultNextResponseHours('Normal'), 8);
      expect(defaultNextResponseHours('Low'), 24);
      expect(defaultNextResponseHours('Whatever'), 8);
    });

    test('is sent on create, and as null when blank', () {
      Map<String, dynamic> create({int? hours}) => escalationCreatePayload(
        priority: 'Urgent',
        firstResponseAction: escalationActionNotify,
        resolutionAction: escalationActionNotify,
        firstResponseTargetId: 'p1',
        resolutionTargetId: 'p1',
        notifyTeamId: null,
        nextResponseHours: hours,
      );
      expect(create(hours: 2)['next_response_hours'], 2);
      final blank = create();
      expect(blank.containsKey('next_response_hours'), isTrue);
      expect(blank['next_response_hours'], isNull);
    });

    test('is sent on update even when null, which clears the override', () {
      Map<String, dynamic> update({int? hours}) => escalationUpdatePayload(
        firstResponseAction: escalationActionNotify,
        resolutionAction: escalationActionNotify,
        firstResponseTargetId: 'p1',
        resolutionTargetId: 'p1',
        notifyTeamId: null,
        nextResponseHours: hours,
      );
      expect(update(hours: 12)['next_response_hours'], 12);
      final blank = update();
      expect(blank.containsKey('next_response_hours'), isTrue);
      expect(blank['next_response_hours'], isNull);
    });
  });

  group('the policy form sends the next response target', () {
    void usePhone(WidgetTester tester, {double textScale = 1.0}) {
      tester.view.devicePixelRatio = 3.0;
      tester.view.physicalSize = const Size(390 * 3, 844 * 3);
      tester.platformDispatcher.textScaleFactorTestValue = textScale;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    }

    /// Opens the sheet over a button and hands back whatever it returned.
    Future<List<Map<String, dynamic>?>> open(
      WidgetTester tester, {
      EscalationPolicy? existing,
      double textScale = 1.0,
    }) async {
      usePhone(tester, textScale: textScale);
      final results = <Map<String, dynamic>?>[];
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            usersProvider.overrideWithValue(const <UserLookup>[]),
            teamsProvider.overrideWithValue(const <TeamLookup>[]),
          ],
          child: MaterialApp(
            home: Scaffold(
              body: Builder(
                builder: (context) => Center(
                  child: ElevatedButton(
                    onPressed: () async => results.add(
                      await showEscalationPolicyFormSheet(
                        context,
                        existing: existing,
                        availablePriorities: const ['Urgent', 'Low'],
                      ),
                    ),
                    child: const Text('open'),
                  ),
                ),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
      return results;
    }

    Finder nextField() =>
        find.widgetWithText(TextField, 'Next response (hours)');

    Future<void> submit(WidgetTester tester, String label) async {
      // Close the keyboard first, as a person would: the focused field's own
      // scroll-into-view otherwise fights the one below.
      FocusManager.instance.primaryFocus?.unfocus();
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text(label));
      await tester.pumpAndSettle();
      await tester.tap(find.text(label));
      await tester.pumpAndSettle();
    }

    for (final scale in [1.0, 1.3]) {
      testWidgets('fits a 390px phone at text scale $scale', (tester) async {
        await open(tester, textScale: scale);
        expect(tester.takeException(), isNull);
        expect(nextField(), findsOneWidget);
        // Urgent is the first free priority, so its default is the hint.
        expect(
          find.descendant(of: nextField(), matching: find.text('1 (default)')),
          findsOneWidget,
        );
        expect(find.textContaining('triggers no escalation'), findsOneWidget);
      });
    }

    testWidgets('an edit keeps a stored override it was not asked to touch', (
      tester,
    ) async {
      // The edit body always carries the key, so a field that opened empty
      // would clear the override as a side effect of editing anything else.
      final results = await open(
        tester,
        existing: EscalationPolicy.fromJson({
          ...policyJson(firstResponseTarget: alice, resolutionTarget: alice),
          'next_response_hours': 3,
        }),
      );
      expect(tester.widget<TextField>(nextField()).controller!.text, '3');

      await submit(tester, 'Save changes');

      expect(results.single!['next_response_hours'], 3);
    });

    testWidgets('a blank field is sent as null, the built-in default', (
      tester,
    ) async {
      final results = await open(tester);
      await submit(tester, 'Add policy');

      expect(results.single!.containsKey('next_response_hours'), isTrue);
      expect(results.single!['next_response_hours'], isNull);
    });

    testWidgets('refuses 0 and 8761 without closing the sheet', (tester) async {
      final results = await open(tester);
      for (final bad in ['0', '8761']) {
        await tester.enterText(nextField(), bad);
        await submit(tester, 'Add policy');
        expect(results, isEmpty);
        expect(find.textContaining('from 1 to 8760'), findsOneWidget);
      }

      await tester.enterText(nextField(), '8760');
      await submit(tester, 'Add policy');
      expect(results.single!['next_response_hours'], 8760);
    });
  });
}
