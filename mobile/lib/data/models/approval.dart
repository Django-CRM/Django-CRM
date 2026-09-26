import 'package:flutter/material.dart';
import '../../core/theme/app_colors.dart';

/// Approval request for a ticket close (Tier 3).
class Approval {
  final String id;
  final ApprovalState state;
  final String? note;
  final String? reason;
  final DateTime? decidedAt;
  final DateTime? createdAt;
  final ApprovalCaseSummary? caseSummary;
  final ApprovalRuleSummary? ruleSummary;
  final ApprovalProfileRef? requestedBy;
  final ApprovalProfileRef? approver;

  /// The viewer may approve or reject this row right now, as the server
  /// decides it (`ApprovalSerializer.get_can_act`): pending, in the rule's
  /// approver pool, and not the requester.
  final bool canAct;

  /// The viewer may withdraw this request right now (`can_cancel`): it is
  /// pending and they filed it or are an org admin, the cancel endpoint's
  /// rule. This, not [isOwnRequest], gates Withdraw.
  final bool canCancel;

  /// The viewer filed this request.
  final bool isOwnRequest;

  const Approval({
    required this.id,
    required this.state,
    this.note,
    this.reason,
    this.decidedAt,
    this.createdAt,
    this.caseSummary,
    this.ruleSummary,
    this.requestedBy,
    this.approver,
    this.canAct = false,
    this.canCancel = false,
    this.isOwnRequest = false,
  });

  bool get isPending => state == ApprovalState.pending;

  factory Approval.fromJson(Map<String, dynamic> json) {
    return Approval(
      id: json['id']?.toString() ?? '',
      state: ApprovalState.fromString(json['state'] as String?),
      note: json['note'] as String?,
      reason: json['reason'] as String?,
      decidedAt: json['decided_at'] != null
          ? DateTime.tryParse(json['decided_at'].toString())
          : null,
      createdAt: json['created_at'] != null
          ? DateTime.tryParse(json['created_at'].toString())
          : null,
      caseSummary: json['case_summary'] is Map<String, dynamic>
          ? ApprovalCaseSummary.fromJson(
              json['case_summary'] as Map<String, dynamic>,
            )
          : null,
      ruleSummary: json['rule_summary'] is Map<String, dynamic>
          ? ApprovalRuleSummary.fromJson(
              json['rule_summary'] as Map<String, dynamic>,
            )
          : null,
      requestedBy: json['requested_by'] is Map<String, dynamic>
          ? ApprovalProfileRef.fromJson(
              json['requested_by'] as Map<String, dynamic>,
            )
          : null,
      approver: json['approver'] is Map<String, dynamic>
          ? ApprovalProfileRef.fromJson(
              json['approver'] as Map<String, dynamic>,
            )
          : null,
      canAct: json['can_act'] == true,
      canCancel: json['can_cancel'] == true,
      isOwnRequest: json['is_own_request'] == true,
    );
  }
}

enum ApprovalState {
  pending('pending', 'Pending', AppColors.warning500),
  approved('approved', 'Approved', AppColors.success600),
  rejected('rejected', 'Rejected', AppColors.danger600),
  cancelled('cancelled', 'Cancelled', AppColors.gray500);

  final String value;
  final String label;
  final Color color;
  const ApprovalState(this.value, this.label, this.color);

  static ApprovalState fromString(String? value) {
    return ApprovalState.values.firstWhere(
      (s) => s.value == value,
      orElse: () => ApprovalState.pending,
    );
  }
}

class ApprovalCaseSummary {
  final String id;
  final String name;
  final String? status;
  final String? priority;
  const ApprovalCaseSummary({
    required this.id,
    required this.name,
    this.status,
    this.priority,
  });
  factory ApprovalCaseSummary.fromJson(Map<String, dynamic> json) {
    return ApprovalCaseSummary(
      id: json['id']?.toString() ?? '',
      name: json['name'] as String? ?? '',
      status: json['status'] as String?,
      priority: json['priority'] as String?,
    );
  }
}

class ApprovalRuleSummary {
  final String id;
  final String name;
  final String? approverRole;
  const ApprovalRuleSummary({
    required this.id,
    required this.name,
    this.approverRole,
  });
  factory ApprovalRuleSummary.fromJson(Map<String, dynamic> json) {
    return ApprovalRuleSummary(
      id: json['id']?.toString() ?? '',
      name: json['name'] as String? ?? '',
      approverRole: json['approver_role'] as String?,
    );
  }
}

class ApprovalProfileRef {
  final String id;
  final String email;
  const ApprovalProfileRef({required this.id, required this.email});
  factory ApprovalProfileRef.fromJson(Map<String, dynamic> json) {
    return ApprovalProfileRef(
      id: json['id']?.toString() ?? '',
      email: json['email'] as String? ?? '',
    );
  }
}
