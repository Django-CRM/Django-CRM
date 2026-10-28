import '../api/enterprise_schema.g.dart' as enterprise;
import 'attachment.dart';
import 'comment.dart';
import 'custom_field_definition.dart';
import 'lookup_models.dart';

const objectsTurnedOffNotice =
    'Creating and editing is turned off for this organization.';

/// The server's cap on a note's length.
const noteMaxLength = 10000;
const objectsLoadFailedNotice =
    'Custom objects could not be loaded. Try again.';

/// Whether a stored custom field value has anything to show.
bool hasFieldValue(dynamic v) =>
    v != null &&
    (v is! String || v.trim().isNotEmpty) &&
    (v is! List || v.isNotEmpty);

/// A stored custom field value as the user reads it. A dropdown shows its
/// option label, or the stored value itself when the option is gone.
String displayValue(ObjectField field, dynamic raw) {
  if (!hasFieldValue(raw)) return 'None';
  if (raw is bool || field.fieldType == 'checkbox') {
    return raw == true ? 'Yes' : 'No';
  }
  if (raw is List) return raw.map((e) => e.toString()).join(', ');
  final text = raw.toString();
  if (field.fieldType == 'dropdown') {
    for (final o in field.options) {
      if (o.value == text) return o.label;
    }
  }
  return text;
}

/// The answer of `GET /api/objects/`. Absent routes (a 404) never reach here.
class CustomObjectsAnswer {
  final bool hasCustomObjects;
  final List<ObjectSummary> objects;
  final List<LinkTarget> linkTargets;

  const CustomObjectsAnswer({
    this.hasCustomObjects = false,
    this.objects = const [],
    this.linkTargets = const [],
  });

  bool get offered => hasCustomObjects || objects.isNotEmpty;

  /// Reads a `CustomObjectList`.
  factory CustomObjectsAnswer.fromJson(Map<String, dynamic> json) {
    final w = enterprise.CustomObjectList.fromJson(json);
    return CustomObjectsAnswer(
      hasCustomObjects: w.hasCustomObjects ?? false,
      objects: [
        for (final o in w.objects ?? const <enterprise.CustomObjectSummary>[])
          ObjectSummary.fromJson(o.json),
      ],
      linkTargets: [
        for (final t in w.linkTargets ?? const <enterprise.CustomObjectRef>[])
          LinkTarget.fromJson(t.json),
      ],
    );
  }
}

class ObjectSummary {
  final String key;
  final String label;
  final String labelPlural;
  final String nameLabel;
  final String description;
  final String recordAccess;
  final bool isActive;

  /// The key of the dropdown field the board groups by, or empty for no
  /// board.
  final String boardField;

  /// The app that installed the object, or empty for one the org designed.
  final String appId;

  /// Whether the caller's org may write to this object; null when the server
  /// does not say, which falls back to the org-wide flag.
  final bool? writable;

  /// Whether the object has a matches panel on its record screen.
  final bool matches;

  const ObjectSummary({
    required this.key,
    required this.label,
    required this.labelPlural,
    this.nameLabel = 'Name',
    this.description = '',
    this.recordAccess = 'everyone',
    this.isActive = true,
    this.boardField = '',
    this.appId = '',
    this.writable,
    this.matches = false,
  });

  /// Reads a `CustomObjectSummary`, which the object's `CustomObjectDetail`
  /// repeats beside its fields.
  factory ObjectSummary.fromJson(Map<String, dynamic> json) {
    final w = enterprise.CustomObjectSummary.fromJson(json);
    final label = w.label ?? '';
    return ObjectSummary(
      key: w.key ?? '',
      label: label,
      labelPlural: w.labelPlural ?? label,
      nameLabel: w.nameLabel ?? 'Name',
      description: w.description ?? '',
      recordAccess: w.recordAccess ?? 'everyone',
      isActive: w.isActive ?? true,
      boardField: w.boardField ?? '',
      appId: w.appId ?? '',
      writable: w.writable,
      matches: w.matches ?? false,
    );
  }
}

/// Whether records of [object] may be created, changed or deleted: the
/// object's own `writable` flag, or the org-wide one when the server sends
/// none. Creating a new object stays on the org flag alone.
bool canWriteObject(ObjectSummary object, CustomObjectsAnswer? answer) =>
    object.writable ?? (answer?.hasCustomObjects == true);

class LinkTarget {
  final String key;
  final String label;
  final String labelPlural;

  const LinkTarget({
    required this.key,
    required this.label,
    required this.labelPlural,
  });

  /// Reads a `CustomObjectRef`.
  factory LinkTarget.fromJson(Map<String, dynamic> json) {
    final w = enterprise.CustomObjectRef.fromJson(json);
    final label = w.label ?? '';
    return LinkTarget(
      key: w.key ?? '',
      label: label,
      labelPlural: w.labelPlural ?? label,
    );
  }
}

class ObjectField {
  final String key;
  final String label;
  final String fieldType;
  final List<CustomFieldOption> options;
  final bool isRequired;
  final int displayOrder;
  final bool isActive;
  final String? target;
  final bool? many;

  const ObjectField({
    required this.key,
    required this.label,
    required this.fieldType,
    this.options = const [],
    this.isRequired = false,
    this.displayOrder = 0,
    this.isActive = true,
    this.target,
    this.many,
  });

  bool get isLink => fieldType == 'link';

  /// Reads a `CustomObjectField`.
  factory ObjectField.fromJson(Map<String, dynamic> json) {
    final w = enterprise.CustomObjectField.fromJson(json);
    return ObjectField(
      key: w.key ?? '',
      label: w.label ?? '',
      fieldType: w.fieldType ?? 'text',
      options: [
        for (final o in w.options ?? const <enterprise.CustomFieldOption>[])
          if (o.value case final value? when value.isNotEmpty)
            CustomFieldOption(value: value, label: o.label ?? value),
      ],
      isRequired: w.isRequired ?? false,
      displayOrder: w.displayOrder ?? 0,
      isActive: w.isActive ?? true,
      target: w.target,
      many: w.many,
    );
  }

  /// The id is empty: a field is addressed by its key, never by id.
  CustomFieldDefinition toDefinition(String objectKey) => CustomFieldDefinition(
    id: '',
    targetModel: 'obj:$objectKey',
    key: key,
    label: label,
    fieldType: CustomFieldType.fromString(fieldType),
    options: options,
    isRequired: isRequired,
    displayOrder: displayOrder,
    isActive: isActive,
  );
}

class ObjectDefinition {
  final ObjectSummary summary;
  final List<ObjectField> fields;

  const ObjectDefinition({required this.summary, this.fields = const []});

  List<ObjectField> _ordered(bool Function(ObjectField f) test) {
    final out = fields.where((f) => f.isActive && test(f)).toList();
    out.sort((a, b) => a.displayOrder.compareTo(b.displayOrder));
    return out;
  }

  /// The active dropdown field the board groups by, or null when the object
  /// has no board (or its field is turned off).
  ObjectField? get boardField {
    final key = summary.boardField;
    if (key.isEmpty) return null;
    for (final f in fields) {
      if (f.key == key && f.isActive && f.fieldType == 'dropdown') return f;
    }
    return null;
  }

  /// The dropdown fields a board can group by.
  List<ObjectField> get boardChoices =>
      _ordered((f) => f.fieldType == 'dropdown');

  List<ObjectField> get customFields => _ordered((f) => !f.isLink);
  List<ObjectField> get linkFields => _ordered((f) => f.isLink);

  /// Reads a `CustomObjectDetail`: the summary with its fields.
  factory ObjectDefinition.fromJson(Map<String, dynamic> json) {
    final w = enterprise.CustomObjectDetail.fromJson(json);
    return ObjectDefinition(
      summary: ObjectSummary.fromJson(json),
      fields: [
        for (final f in w.fields ?? const <enterprise.CustomObjectField>[])
          ObjectField.fromJson(f.json),
      ],
    );
  }
}

class PersonRef {
  final String id;
  final String name;
  final String email;

  const PersonRef({required this.id, this.name = '', this.email = ''});

  /// Reads a `CustomRecordPerson`.
  factory PersonRef.fromJson(Map<String, dynamic> json) {
    final w = enterprise.CustomRecordPerson.fromJson(json);
    return PersonRef(id: w.id ?? '', name: w.name ?? '', email: w.email ?? '');
  }
}

class LinkedRecord {
  final String id;
  final String object;
  final String name;

  const LinkedRecord({required this.id, required this.object, this.name = ''});

  /// Reads a `CustomRecordLinked`.
  factory LinkedRecord.fromJson(Map<String, dynamic> json) {
    final w = enterprise.CustomRecordLinked.fromJson(json);
    return LinkedRecord(
      id: w.id ?? '',
      object: w.object ?? '',
      name: w.name ?? '',
    );
  }
}

class LinkValue {
  final bool many;
  final List<LinkedRecord> records;
  final int hidden;

  const LinkValue({this.many = true, this.records = const [], this.hidden = 0});

  /// A one-record field holding a link the caller cannot open is never sent.
  bool get readOnly => !many && hidden > 0;

  /// Reads a `CustomRecordLinkValue`.
  factory LinkValue.fromJson(Map<String, dynamic> json) {
    final w = enterprise.CustomRecordLinkValue.fromJson(json);
    return LinkValue(
      many: w.many ?? true,
      records: [
        for (final r in w.records ?? const <enterprise.CustomRecordLinked>[])
          LinkedRecord.fromJson(r.json),
      ],
      hidden: w.hidden ?? 0,
    );
  }
}

class RecordAttachment {
  static const photoExtensions = {'jpg', 'jpeg', 'png', 'webp', 'gif'};

  final String id;
  final String fileName;
  final bool isImage;
  final PersonRef? createdBy;
  final DateTime? createdAt;
  final bool canDelete;

  const RecordAttachment({
    required this.id,
    required this.fileName,
    this.isImage = false,
    this.createdBy,
    this.createdAt,
    this.canDelete = false,
  });

  bool get isPhoto {
    if (!isImage) return false;
    final dot = fileName.lastIndexOf('.');
    if (dot < 0) return false;
    return photoExtensions.contains(fileName.substring(dot + 1).toLowerCase());
  }

  /// A record's file is always fetched by id. The name stands in for
  /// `filePath` only so `hasFile` is true; it is never read as a path.
  Attachment toAttachment() => Attachment(
    id: id,
    fileName: fileName,
    filePath: fileName,
    createdAt: createdAt,
    createdBy: createdBy == null
        ? null
        : (createdBy!.email.isNotEmpty ? createdBy!.email : createdBy!.name),
  );

  /// Reads a `CustomRecordAttachment`.
  factory RecordAttachment.fromJson(Map<String, dynamic> json) {
    final w = enterprise.CustomRecordAttachment.fromJson(json);
    final by = w.createdBy;
    return RecordAttachment(
      id: w.id ?? '',
      fileName: w.fileName ?? 'Attachment',
      isImage: w.isImage ?? false,
      createdBy: by == null ? null : PersonRef.fromJson(by.json),
      createdAt: DateTime.tryParse(w.createdAt ?? ''),
      canDelete: w.canDelete ?? false,
    );
  }
}

class ObjectRecord {
  final String id;
  final String name;
  final Map<String, dynamic> customFields;
  final List<PersonRef> assignedTo;
  final DateTime? createdAt;
  final DateTime? updatedAt;
  final Map<String, LinkValue> links;
  final PersonRef? createdBy;
  final bool canEdit;
  final bool canDelete;
  final List<RecordAttachment> attachments;

  /// The record's notes, newest first. An older server sends none.
  final List<Comment> comments;

  /// The record's tags. An older server sends none, which reads as no tags.
  final List<TagLookup> tags;

  const ObjectRecord({
    required this.id,
    required this.name,
    this.customFields = const {},
    this.assignedTo = const [],
    this.createdAt,
    this.updatedAt,
    this.links = const {},
    this.createdBy,
    this.canEdit = false,
    this.canDelete = false,
    this.attachments = const [],
    this.comments = const [],
    this.tags = const [],
  });

  /// Reads a `CustomRecordDetail`. A list row is a `CustomRecord`, the same
  /// minus links, creator, rights and attachments, which then read as none.
  factory ObjectRecord.fromJson(Map<String, dynamic> json) {
    final w = enterprise.CustomRecordDetail.fromJson(json);
    final by = w.createdBy;
    final links = w.links ?? const <String, enterprise.CustomRecordLinkValue>{};
    return ObjectRecord(
      id: w.id ?? '',
      name: w.name ?? '',
      customFields: {...?w.customFields},
      assignedTo: [
        for (final p in w.assignedTo ?? const <enterprise.CustomRecordPerson>[])
          PersonRef.fromJson(p.json),
      ],
      createdAt: DateTime.tryParse(w.createdAt ?? ''),
      updatedAt: DateTime.tryParse(w.updatedAt ?? ''),
      links: {
        for (final e in links.entries) e.key: LinkValue.fromJson(e.value.json),
      },
      createdBy: by == null ? null : PersonRef.fromJson(by.json),
      canEdit: w.canEdit ?? false,
      canDelete: w.canDelete ?? false,
      attachments: [
        for (final a
            in w.attachments ?? const <enterprise.CustomRecordAttachment>[])
          RecordAttachment.fromJson(a.json),
      ],
      comments: [
        for (final c in w.comments ?? const <enterprise.Comment>[])
          Comment.fromJson(c.json),
      ],
      tags: [
        for (final t in w.tags ?? const <enterprise.Tags>[])
          if (t.id != null)
            TagLookup(
              id: t.id!,
              name: t.name ?? '',
              slug: t.slug ?? '',
              color: t.color ?? 'gray',
            ),
      ],
    );
  }
}

class RelatedRecordRef {
  final String id;
  final String name;

  const RelatedRecordRef({required this.id, this.name = ''});

  /// Reads a `CustomRecordName`.
  factory RelatedRecordRef.fromJson(Map<String, dynamic> json) {
    final w = enterprise.CustomRecordName.fromJson(json);
    return RelatedRecordRef(id: w.id ?? '', name: w.name ?? '');
  }
}

class RelatedGroup {
  final String objectKey;
  final String objectLabel;
  final String fieldKey;
  final String fieldLabel;
  final List<RelatedRecordRef> records;

  const RelatedGroup({
    required this.objectKey,
    required this.objectLabel,
    required this.fieldKey,
    required this.fieldLabel,
    this.records = const [],
  });

  /// Reads a `RelatedGroup`.
  factory RelatedGroup.fromJson(Map<String, dynamic> json) {
    final w = enterprise.RelatedGroup.fromJson(json);
    return RelatedGroup(
      objectKey: w.object?.key ?? '',
      objectLabel: w.object?.label ?? '',
      fieldKey: w.field?.key ?? '',
      fieldLabel: w.field?.label ?? '',
      records: [
        for (final r in w.records ?? const <enterprise.CustomRecordName>[])
          RelatedRecordRef.fromJson(r.json),
      ],
    );
  }
}

class RelatedLinkField {
  final String objectKey;
  final String objectLabel;
  final String fieldKey;
  final String fieldLabel;

  const RelatedLinkField({
    required this.objectKey,
    required this.objectLabel,
    required this.fieldKey,
    required this.fieldLabel,
  });

  /// Reads a `RelatedLinkField`.
  factory RelatedLinkField.fromJson(Map<String, dynamic> json) {
    final w = enterprise.RelatedLinkField.fromJson(json);
    return RelatedLinkField(
      objectKey: w.object?.key ?? '',
      objectLabel: w.object?.label ?? '',
      fieldKey: w.field?.key ?? '',
      fieldLabel: w.field?.label ?? '',
    );
  }
}

class RelatedAnswer {
  final List<RelatedGroup> groups;
  final bool canEdit;
  final List<RelatedLinkField> linkFields;

  const RelatedAnswer({
    this.groups = const [],
    this.canEdit = false,
    this.linkFields = const [],
  });

  /// A record can be taken out of a group only where the caller may edit and
  /// the field is one of the many-record fields the server listed.
  bool removable(RelatedGroup g) =>
      canEdit &&
      linkFields.any(
        (f) => f.objectKey == g.objectKey && f.fieldKey == g.fieldKey,
      );

  bool get showsNothing => groups.isEmpty && linkFields.isEmpty;

  /// Reads a `RelatedRecords`.
  factory RelatedAnswer.fromJson(Map<String, dynamic> json) {
    final w = enterprise.RelatedRecords.fromJson(json);
    return RelatedAnswer(
      groups: [
        for (final g in w.groups ?? const <enterprise.RelatedGroup>[])
          RelatedGroup.fromJson(g.json),
      ],
      canEdit: w.canEdit ?? false,
      linkFields: [
        for (final f in w.linkFields ?? const <enterprise.RelatedLinkField>[])
          RelatedLinkField.fromJson(f.json),
      ],
    );
  }
}

/// Latin letters that decompose to a base letter plus a combining mark, which
/// the web's `normalize('NFKD')` strips.
const _accentGroups = {
  'a': 'àáâãäåāăą',
  'c': 'çćĉċč',
  'd': 'ďđ',
  'e': 'èéêëēĕėęě',
  'g': 'ĝğġģ',
  'i': 'ìíîïĩīĭįı',
  'l': 'ĺļľł',
  'n': 'ñńņň',
  'o': 'òóôõöøōŏő',
  'r': 'ŕŗř',
  's': 'śŝşš',
  't': 'ţť',
  'u': 'ùúûüũūŭůűų',
  'y': 'ýÿ',
  'z': 'źżž',
};

final _accentMap = {
  for (final e in _accentGroups.entries)
    for (final r in e.value.runes) String.fromCharCode(r): e.key,
};

/// The web's `suggestKey`: accents stripped, runs of anything but a-z0-9
/// become one underscore, the leading non-letters and trailing underscore go.
String suggestObjectKey(String label, int max) {
  final buf = StringBuffer();
  for (final r in label.toLowerCase().runes) {
    final ch = String.fromCharCode(r);
    buf.write(_accentMap[ch] ?? ch);
  }
  var s = buf
      .toString()
      .replaceAll(RegExp(r'[^a-z0-9]+'), '_')
      .replaceFirst(RegExp(r'^[^a-z]+'), '');
  if (s.length > max) s = s.substring(0, max);
  return s.replaceFirst(RegExp(r'_+$'), '');
}

/// Whether the viewer wrote [note]. The author's email is the one identifier
/// the comment and the session share.
bool isNoteAuthor(Comment note, String? myEmail) =>
    myEmail != null &&
    myEmail.isNotEmpty &&
    note.commentedByEmail?.toLowerCase() == myEmail.toLowerCase();

/// Only the author edits a note.
bool canEditNote(Comment note, String? myEmail) => isNoteAuthor(note, myEmail);

/// The author or an admin deletes a note.
bool canDeleteNote(Comment note, String? myEmail, {required bool isAdmin}) =>
    isAdmin || isNoteAuthor(note, myEmail);
