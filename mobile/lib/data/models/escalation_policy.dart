/// What happens when a ticket blows its SLA target.
///
/// One `EscalationPolicy` per priority, enforced by a `(org, priority)` unique
/// constraint, and each policy has two independent halves: what to do when the
/// first response is late, and what to do when the resolution is. The useful
/// question is never "is there a policy", there is at most one per priority. It
/// is "does it fire", and the model allows several ways for the answer to be no
/// while the row still looks configured.
library;

import 'lookup_models.dart';

/// `EscalationPolicy.ACTION_CHOICES`.
const String escalationActionNotify = 'notify';
const String escalationActionReassign = 'reassign';
const String escalationActionNotifyAndReassign = 'notify_and_reassign';

const Map<String, String> escalationActionLabels = {
  escalationActionNotify: 'Notify',
  escalationActionReassign: 'Reassign to',
  escalationActionNotifyAndReassign: 'Notify and reassign to',
};

String escalationActionLabel(String action) =>
    escalationActionLabels[action] ?? action;

/// Whether an action emails anybody.
///
/// `cases/tasks.py::_dispatch_breach` builds a recipient list only for `notify`
/// and `notify_and_reassign`. A `reassign` half moves the ticket and sends no
/// mail at all, so the notify team plays no part in it.
bool escalationActionNotifies(String action) =>
    action == escalationActionNotify ||
    action == escalationActionNotifyAndReassign;

/// The four priorities, worst first.
///
/// Not the model's `ordering = ("priority",)`, which sorts the CharField
/// alphabetically and puts Low between High and Normal. That reads as a mistake
/// every time, so both clients sort by severity instead.
const List<String> escalationPriorities = ['Urgent', 'High', 'Normal', 'Low'];

/// The two halves of an SLA, which are configured separately and fail
/// separately.
enum EscalationHalf {
  firstResponse('Missed first response'),
  resolution('Missed resolution');

  const EscalationHalf(this.label);

  final String label;
}

/// Breaches per half among tickets opened in the last 30 days.
///
/// Server-computed from the breach *condition* on each ticket, not from the
/// ESCALATED activity log. That distinction is the page: a dead policy records
/// no activity, so an event-sourced count would report zero breaches for
/// exactly the policies worth looking at.
class EscalationBreachCounts {
  const EscalationBreachCounts({this.firstResponse = 0, this.resolution = 0});

  final int firstResponse;
  final int resolution;

  int forHalf(EscalationHalf half) => switch (half) {
    EscalationHalf.firstResponse => firstResponse,
    EscalationHalf.resolution => resolution,
  };

  factory EscalationBreachCounts.fromJson(dynamic json) {
    if (json is! Map) return const EscalationBreachCounts();
    return EscalationBreachCounts(
      firstResponse: json['first_response'] as int? ?? 0,
      resolution: json['resolution'] as int? ?? 0,
    );
  }
}

class EscalationPolicy {
  const EscalationPolicy({
    required this.id,
    required this.priority,
    this.isActive = true,
    this.firstResponseAction = escalationActionNotify,
    this.resolutionAction = escalationActionNotify,
    this.firstResponseTarget,
    this.resolutionTarget,
    this.notifyTeam,
    this.firstResponseHours,
    this.resolutionHours,
    this.breaches = const EscalationBreachCounts(),
  });

  final String id;
  final String priority;
  final bool isActive;

  final String firstResponseAction;
  final String resolutionAction;

  /// Whoever the breach is escalated to. Nullable in the model and emptied
  /// silently by `on_delete=SET_NULL` when a profile is removed, which is one
  /// of the ways a configured-looking policy stops doing anything.
  final UserLookup? firstResponseTarget;
  final UserLookup? resolutionTarget;

  /// One team, shared by both halves, and only ever a CC. See [firesFor].
  final TeamLookup? notifyTeam;

  /// The SLA this org promises at this priority, in hours.
  ///
  /// Null means the org never set one and cases fall back to the built-in
  /// table in `cases/workflow.py`. Kept null rather than filled in with the
  /// default on parse, so the UI can say which of the two a number is.
  final int? firstResponseHours;
  final int? resolutionHours;

  final EscalationBreachCounts breaches;

  String actionFor(EscalationHalf half) => switch (half) {
    EscalationHalf.firstResponse => firstResponseAction,
    EscalationHalf.resolution => resolutionAction,
  };

  UserLookup? targetFor(EscalationHalf half) => switch (half) {
    EscalationHalf.firstResponse => firstResponseTarget,
    EscalationHalf.resolution => resolutionTarget,
  };

  int breachesFor(EscalationHalf half) => breaches.forHalf(half);

  int? hoursFor(EscalationHalf half) => switch (half) {
    EscalationHalf.firstResponse => firstResponseHours,
    EscalationHalf.resolution => resolutionHours,
  };

  /// "6h" for a target this org set, "4h default" for one it never touched.
  /// The distinction is the point: an unlabelled number reads as a decision.
  String targetLabelFor(EscalationHalf half) {
    final configured = hoursFor(half);
    if (configured != null) return '${configured}h';
    return '${defaultSlaHours(priority, half)}h default';
  }

  String get firstResponseTargetLabel =>
      targetLabelFor(EscalationHalf.firstResponse);
  String get resolutionTargetLabel => targetLabelFor(EscalationHalf.resolution);

  /// Whether this half does anything at all when a ticket breaches.
  ///
  /// **A half with no target does nothing, whatever its action and whatever the
  /// notify team.** `cases/tasks.py::_scan_org` guards both dispatches with
  /// `if <breach> and policy.<half>_target_id is not None`, so with the target
  /// null `_dispatch_breach` is never reached: no mail, no reassignment, no
  /// ESCALATED activity. The team is a CC built *inside* that dispatch, not a
  /// recipient that can stand on its own, so "notify the Support team, nobody
  /// in particular" notifies nobody.
  ///
  /// An inactive policy is skipped whole: `_scan_org` reads
  /// `EscalationPolicy.objects.filter(org=org, is_active=True)`.
  bool firesFor(EscalationHalf half) => isActive && targetFor(half) != null;

  /// Whether the notify team actually hears about this half.
  bool notifiesTeamFor(EscalationHalf half) =>
      firesFor(half) &&
      notifyTeam != null &&
      escalationActionNotifies(actionFor(half));

  /// A policy that does nothing on either half.
  bool get isDead =>
      !firesFor(EscalationHalf.firstResponse) &&
      !firesFor(EscalationHalf.resolution);

  /// Breaches in the last 30 days that reached nobody, counting only the halves
  /// that cannot fire.
  int get breachesGoingNowhere => EscalationHalf.values
      .where((half) => !firesFor(half))
      .fold(0, (sum, half) => sum + breachesFor(half));

  /// Targets whose account is deactivated.
  ///
  /// The engine escalates to them with no active filter, so a breach reassigned
  /// here waits for somebody who cannot sign in. The picker only lists active
  /// profiles, so the form has to keep these deliberately.
  List<UserLookup> get deactivatedTargets => [
    for (final half in EscalationHalf.values)
      if (targetFor(half) != null && !targetFor(half)!.isActive)
        targetFor(half)!,
  ];

  factory EscalationPolicy.fromJson(Map<String, dynamic> json) {
    UserLookup? person(dynamic value) =>
        value is Map<String, dynamic> ? UserLookup.fromJson(value) : null;
    final team = json['notify_team'];
    return EscalationPolicy(
      id: json['id']?.toString() ?? '',
      priority: json['priority']?.toString() ?? '',
      isActive: json['is_active'] as bool? ?? true,
      firstResponseAction:
          json['first_response_action']?.toString() ?? escalationActionNotify,
      resolutionAction:
          json['resolution_action']?.toString() ?? escalationActionNotify,
      firstResponseTarget: person(json['first_response_target']),
      resolutionTarget: person(json['resolution_target']),
      notifyTeam: team is Map<String, dynamic>
          ? TeamLookup.fromJson(team)
          : null,
      firstResponseHours: json['first_response_hours'] as int?,
      resolutionHours: json['resolution_hours'] as int?,
      breaches: EscalationBreachCounts.fromJson(json['breaches_last_30d']),
    );
  }
}

/// `cases/workflow.py` DEFAULT_FIRST_RESPONSE_SLA and DEFAULT_RESOLUTION_SLA.
/// What a case gets when its org set no target of its own. Mirrored here so a
/// policy card can name the number behind a blank field instead of leaving it
/// looking like there is no SLA at all. Falls back to the Normal row for a
/// priority this table does not know.
const Map<String, ({int firstResponse, int resolution})> defaultSlaHoursTable =
    {
      'Urgent': (firstResponse: 1, resolution: 4),
      'High': (firstResponse: 4, resolution: 24),
      'Normal': (firstResponse: 8, resolution: 48),
      'Low': (firstResponse: 24, resolution: 72),
    };

int defaultSlaHours(String priority, EscalationHalf half) {
  final row = defaultSlaHoursTable[priority] ?? defaultSlaHoursTable['Normal']!;
  return switch (half) {
    EscalationHalf.firstResponse => row.firstResponse,
    EscalationHalf.resolution => row.resolution,
  };
}

/// Mirrors `MAX_SLA_HOURS` in `cases/workflow.py`. The bound exists because the
/// business-hours walker gives up after 5 years of calendar days.
const int maxSlaHours = 8760;

/// Text field → what the API wants, or an error the form shows.
///
/// Blank clears the override and falls back to the built-in default, so it has
/// to reach the server as null and never as `0`: zero puts the deadline on
/// `created_at`, opening every new case already breached. The serializer
/// rejects both; this is the faster message, not the rule.
({int? hours, String? error}) parseSlaHours(String raw) {
  final trimmed = raw.trim();
  if (trimmed.isEmpty) return (hours: null, error: null);
  final parsed = int.tryParse(trimmed);
  if (parsed == null || parsed < 1 || parsed > maxSlaHours) {
    return (
      hours: null,
      error:
          'Enter a whole number of hours from 1 to $maxSlaHours, '
          'or leave it blank for the default.',
    );
  }
  return (hours: parsed, error: null);
}

/// Worst priority first, whatever order the server sent.
List<EscalationPolicy> sortedEscalationPolicies(List<EscalationPolicy> given) {
  final policies = [...given];
  policies.sort((a, b) {
    final rank = escalationPriorities
        .indexOf(a.priority)
        .compareTo(escalationPriorities.indexOf(b.priority));
    return rank != 0 ? rank : a.priority.compareTo(b.priority);
  });
  return policies;
}

/// Priorities with no policy at all.
///
/// Two things read this. "New policy" may only offer these, because the unique
/// constraint (and `EscalationPolicySerializer.validate`) refuses a second
/// policy for a priority. And the screen says out loud that breaches at these
/// priorities escalate to nobody, which is otherwise invisible: the API attaches
/// `breaches_last_30d` to policies, so an unconfigured priority contributes no
/// row and no number anywhere.
List<String> unconfiguredEscalationPriorities(List<EscalationPolicy> policies) {
  final taken = policies.map((p) => p.priority).toSet();
  return [
    for (final priority in escalationPriorities)
      if (!taken.contains(priority)) priority,
  ];
}

/// "the Support team", but "the Support Team" when the name already says so.
///
/// Seeded orgs name their team "Support Team", and appending the word
/// unconditionally reads "the Support Team team". The suffix is what makes a
/// team named "Support" read as a team rather than a person, so it stays for
/// every other name.
String teamPhrase(String name) {
  final trimmed = name.trim();
  final lower = trimmed.toLowerCase();
  final saysSo =
      lower == 'team' ||
      lower == 'teams' ||
      lower.endsWith(' team') ||
      lower.endsWith(' teams');
  return saysSo ? 'the $trimmed' : 'the $trimmed team';
}

/// "Urgent and High", "Urgent, High and Normal".
String joinWithAnd(List<String> parts) {
  if (parts.isEmpty) return '';
  if (parts.length == 1) return parts.first;
  return '${parts.sublist(0, parts.length - 1).join(', ')} and ${parts.last}';
}

/// What one half of a policy does, in a sentence, including when that is
/// nothing.
String escalationOutcomeSentence(EscalationPolicy policy, EscalationHalf half) {
  if (!policy.isActive) return 'Nothing. The policy is turned off.';
  final target = policy.targetFor(half);
  if (target == null) {
    final team = policy.notifyTeam;
    // Naming the team here is the correction, not decoration. A team set with
    // no target reads as "somebody is told" and is the case where nobody is.
    return team == null
        ? 'Nothing. No target is set.'
        : 'Nothing. No target is set, and ${teamPhrase(team.name)} is not '
              'notified on its own.';
  }
  final label = escalationActionLabel(policy.actionFor(half));
  if (policy.notifiesTeamFor(half)) {
    return '$label ${target.displayName} and '
        '${teamPhrase(policy.notifyTeam!.name)}.';
  }
  return '$label ${target.displayName}.';
}

/// A team configured on a half that will never notify it, or `null`.
///
/// Only for a half that fires: when it does not, the outcome sentence has
/// already said the team is not involved and repeating it would be noise.
String? escalationTeamIgnoredNote(
  EscalationPolicy policy,
  EscalationHalf half,
) {
  final team = policy.notifyTeam;
  if (team == null || !policy.firesFor(half)) return null;
  if (escalationActionNotifies(policy.actionFor(half))) return null;
  final phrase = teamPhrase(team.name);
  return '${phrase[0].toUpperCase()}${phrase.substring(1)} is not notified '
      'here: this half only reassigns.';
}

/// The warning a form shows for a half with no target picked yet.
///
/// Not tied to the action. Picking Notify and leaving the target empty is the
/// same dead half as picking Reassign and leaving it empty, and the action
/// select is the control an admin is most likely to believe fixed it.
const String escalationNoTargetHint =
    'Nothing happens on this half until a target is picked. A team on its own '
    'is not notified.';

/// `''` means "Nobody" in the pickers, and the API wants `null` for that.
String? _orNull(String? id) {
  final trimmed = id?.trim() ?? '';
  return trimmed.isEmpty ? null : trimmed;
}

/// The body for `POST /cases/escalation-policies/`.
Map<String, dynamic> escalationCreatePayload({
  required String priority,
  required String firstResponseAction,
  required String resolutionAction,
  required String? firstResponseTargetId,
  required String? resolutionTargetId,
  required String? notifyTeamId,
  int? firstResponseHours,
  int? resolutionHours,
  bool isActive = true,
}) => {
  'priority': priority,
  'first_response_action': firstResponseAction,
  'resolution_action': resolutionAction,
  'first_response_target_id': _orNull(firstResponseTargetId),
  'resolution_target_id': _orNull(resolutionTargetId),
  'notify_team_id': _orNull(notifyTeamId),
  // Sent even when null: null is the instruction to clear the override, so
  // omitting it would leave a target the admin just blanked in place.
  'first_response_hours': firstResponseHours,
  'resolution_hours': resolutionHours,
  'is_active': isActive,
};

/// The body for `PUT /cases/escalation-policies/<id>/`.
///
/// **`priority` is omitted, and not merely because changing it is unwise.**
/// `EscalationPolicyDetailView.put` deletes the key from the body before the
/// serializer sees it, so a PUT carrying a new priority answers 200 and changes
/// nothing. A control whose success is a lie is worse than no control, so the
/// form does not offer one.
///
/// `is_active` is omitted too: the row's own Turn off and Turn on controls own
/// that state, and resending it from a form opened before a toggle would undo
/// the toggle.
Map<String, dynamic> escalationUpdatePayload({
  required String firstResponseAction,
  required String resolutionAction,
  required String? firstResponseTargetId,
  required String? resolutionTargetId,
  required String? notifyTeamId,
  int? firstResponseHours,
  int? resolutionHours,
}) => {
  'first_response_action': firstResponseAction,
  'resolution_action': resolutionAction,
  'first_response_target_id': _orNull(firstResponseTargetId),
  'resolution_target_id': _orNull(resolutionTargetId),
  'notify_team_id': _orNull(notifyTeamId),
  // See the create payload: null clears the override, so the key goes either
  // way rather than being dropped when it is empty.
  'first_response_hours': firstResponseHours,
  'resolution_hours': resolutionHours,
};

/// Turning a policy on or off, as its own one-key body.
///
/// `put` is `partial=True`, so this is legal, and it has to be built here
/// rather than by reusing the edit body: a row control has no target selects to
/// read, so a full payload built from it would send `first_response_target_id:
/// null` and empty the policy as a side effect of turning it back on. The web's
/// reactivate action had to be split out of its update action for exactly this.
Map<String, dynamic> escalationActivePayload(bool isActive) => {
  'is_active': isActive,
};

/// Deleting a policy is permanent.
///
/// `EscalationPolicyDetailView.delete` calls `obj.delete()`. There is no soft
/// delete behind this verb, unlike custom fields and tags, so the control that
/// calls it says so and offers turning the policy off as the reversible
/// alternative.
const String escalationRemovalExplanation =
    'The policy is gone for good. Breaches at this priority will escalate to '
    'nobody, and the priority goes back to having no policy at all. If you only '
    'want it to stop firing, turn it off instead: that is reversible.';
