/// Comment model for notes on leads, deals, etc.
class Comment {
  final String id;
  final String comment;
  final DateTime commentedOn;
  final String? commentedById;
  final String? commentedByName;
  final String? commentedByEmail;

  /// The customer who wrote this, when it came from the support portal.
  ///
  /// A portal reply has no Profile behind it, so `commented_by` is null on it
  /// and the two author fields are mutually exclusive. Without this the phone
  /// showed a customer's message as written by "Unknown".
  final String? commentedByContactName;

  const Comment({
    required this.id,
    required this.comment,
    required this.commentedOn,
    this.commentedById,
    this.commentedByName,
    this.commentedByEmail,
    this.commentedByContactName,
  });

  /// Get display name for the commenter
  String get authorName {
    if (commentedByName != null && commentedByName!.isNotEmpty) {
      return commentedByName!;
    }
    if (commentedByEmail != null && commentedByEmail!.isNotEmpty) {
      return commentedByEmail!.split('@').first;
    }
    if (commentedByContactName != null && commentedByContactName!.isNotEmpty) {
      return commentedByContactName!;
    }
    return 'Unknown';
  }

  /// Factory constructor to create Comment from JSON
  factory Comment.fromJson(Map<String, dynamic> json) {
    // Handle commented_by which can be a map or an ID
    String? commentedById;
    String? commentedByName;
    String? commentedByEmail;

    final commentedBy = json['commented_by'];
    if (commentedBy is Map<String, dynamic>) {
      commentedById = commentedBy['id']?.toString();
      // The case-app CommentSerializer wraps the email under user_details.
      // Older serializers expose user__email / first_name / last_name at the
      // top level. Keep both paths for compatibility across entities.
      final userDetails = commentedBy['user_details'];
      if (userDetails is Map<String, dynamic>) {
        commentedByEmail = userDetails['email'] as String?;
      }
      commentedByEmail ??=
          commentedBy['user__email'] as String? ??
          commentedBy['email'] as String?;
      commentedByName =
          commentedBy['user__first_name'] as String? ??
          commentedBy['first_name'] as String?;
      if (commentedByName != null) {
        final lastName =
            commentedBy['user__last_name'] as String? ??
            commentedBy['last_name'] as String?;
        if (lastName != null && lastName.isNotEmpty) {
          commentedByName = '$commentedByName $lastName';
        }
      }
    } else if (commentedBy != null) {
      commentedById = commentedBy.toString();
    }

    // Portal replies carry the contact instead. The backend serializer sends a
    // pre-joined `name`, so there is nothing to assemble here.
    String? commentedByContactName;
    final commentedByContact = json['commented_by_contact'];
    if (commentedByContact is Map<String, dynamic>) {
      final name = commentedByContact['name'] as String?;
      if (name != null && name.isNotEmpty) {
        commentedByContactName = name;
      }
      commentedByContactName ??= commentedByContact['email'] as String?;
    }

    return Comment(
      id: json['id']?.toString() ?? '',
      comment: json['comment'] as String? ?? '',
      commentedOn: json['commented_on'] != null
          ? DateTime.tryParse(json['commented_on'] as String) ?? DateTime.now()
          : DateTime.now(),
      commentedById: commentedById,
      commentedByName: commentedByName,
      commentedByEmail: commentedByEmail,
      commentedByContactName: commentedByContactName,
    );
  }

  /// Convert Comment to JSON
  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'comment': comment,
      'commented_on': commentedOn.toIso8601String(),
      'commented_by': commentedById,
    };
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is Comment && runtimeType == other.runtimeType && id == other.id;

  @override
  int get hashCode => id.hashCode;
}
