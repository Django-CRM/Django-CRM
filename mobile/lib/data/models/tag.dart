/// A tag, from `TagsSerializer`, carrying the usage block the settings list
/// attaches to every row.
///
/// A tag list without usage counts is a list of words. The two questions an
/// admin has are "which of these is nobody using" and "have we ended up with
/// two tags for one idea", and neither can be answered without the counts.
class Tag {
  const Tag({
    required this.id,
    this.name = '',
    this.slug = '',
    this.color = 'gray',
    this.description = '',
    this.isActive = true,
    this.usage = const {},
  });

  final String id;
  final String name;

  /// Derived from the name server-side and unique per org, so two tags can
  /// never collide on it. That is exactly why duplicates happen: "Renewal" and
  /// "Renewals" slug differently while meaning the same thing, and the work
  /// splits silently across them. See [duplicateTagGroups].
  final String slug;

  final String color;
  final String description;
  final bool isActive;

  /// `{model key: count}`, one entry per model a tag can be applied to,
  /// computed server-side as org-scoped subqueries.
  ///
  /// Kept as a map rather than seven named fields, and summed by [used] over
  /// whatever keys arrive. The server's `_TAGGABLE` registry held four of the
  /// seven models once, and a tag in real use on contacts reported as unused,
  /// which is the one number an admin uses to decide whether a tag is safe to
  /// turn off. A client that names the keys itself re-creates that bug on its
  /// own schedule.
  final Map<String, int> usage;

  /// Records carrying this tag, across every model the server counted.
  int get used => usage.values.fold(0, (sum, n) => sum + n);

  /// The non-zero parts of [usage], largest first, for the row's summary line.
  List<MapEntry<String, int>> get usageBreakdown {
    final entries = usage.entries.where((e) => e.value > 0).toList()
      ..sort((a, b) {
        final byCount = b.value.compareTo(a.value);
        return byCount != 0
            ? byCount
            : tagUsageLabel(
                a.key,
              ).toLowerCase().compareTo(tagUsageLabel(b.key).toLowerCase());
      });
    return entries;
  }

  factory Tag.fromJson(Map<String, dynamic> json) {
    final rawUsage = json['usage'];
    return Tag(
      id: json['id']?.toString() ?? '',
      name: json['name'] as String? ?? '',
      slug: json['slug'] as String? ?? '',
      color: json['color'] as String? ?? 'gray',
      description: json['description'] as String? ?? '',
      isActive: json['is_active'] as bool? ?? true,
      usage: rawUsage is Map
          ? {
              for (final entry in rawUsage.entries)
                if (entry.value is int)
                  entry.key.toString(): entry.value as int,
            }
          : const {},
    );
  }
}

/// The record type behind a usage key, in the words the rest of the app uses
/// ("deals", not "opportunities").
///
/// An unrecognised key falls back to itself rather than being dropped, so a
/// model added to `_TAGGABLE` later shows up as an ugly label instead of
/// silently vanishing from a breakdown whose total still counts it.
String tagUsageLabel(String key) {
  const labels = {
    'accounts': 'accounts',
    'contacts': 'contacts',
    'leads': 'leads',
    'opportunities': 'deals',
    'cases': 'tickets',
    'tasks': 'tasks',
    'api_settings': 'API keys',
  };
  return labels[key] ?? key.replaceAll('_', ' ');
}

/// Where a tag is actually used, as one line: "12 leads, 3 deals, 1 ticket".
///
/// The web has a column per record type and room for zeroes; a phone row has
/// neither, so this drops the empty ones and keeps the order [Tag.used] adds
/// them up in. Every non-zero part is listed rather than a top few, so the
/// parts always sum to the total shown beside them.
String tagUsageSummary(Tag tag) {
  final parts = [
    for (final entry in tag.usageBreakdown)
      '${entry.value} ${_singularise(tagUsageLabel(entry.key), entry.value)}',
  ];
  return parts.join(', ');
}

/// The usage labels are plural nouns, and every one of them singularises by
/// dropping the final "s" ("API keys" included).
String _singularise(String label, int count) {
  if (count != 1 || !label.endsWith('s')) return label;
  return label.substring(0, label.length - 1);
}

/// Tags ranked the way the list shows them: most used first, then by name.
List<Tag> sortedTags(List<Tag> tags) {
  return [...tags]..sort((a, b) {
    final byUse = b.used.compareTo(a.used);
    return byUse != 0
        ? byUse
        : a.name.toLowerCase().compareTo(b.name.toLowerCase());
  });
}

/// Two names reduced to the same string are probably the same idea.
///
/// Deliberately crude, and identical to the web's `normalise` so both clients
/// flag the same pairs: case, spacing, punctuation and a trailing plural. It
/// decides nothing on its own. It puts two names next to each other and offers
/// the merge, because "Invoice" and "Invoices" might genuinely be two ideas in
/// some org.
///
/// Letters, marks and digits in any script survive. Keeping only a-z and 0-9
/// reduces every Japanese or Cyrillic name to "", which would offer all of an
/// org's non-Latin tags as one duplicate group, with a merge button, now that
/// the server lets them coexist.
String normalizeTagName(String name) => name
    .toLowerCase()
    .replaceAll(RegExp(r'[^\p{L}\p{M}\p{N}]', unicode: true), '')
    .replaceAll(RegExp(r's$'), '');

/// A set of tags that look like the same tag, ranked so the merge runs the
/// right way.
class TagDuplicateGroup {
  const TagDuplicateGroup(this.all);

  /// Most used first, then by name.
  final List<Tag> all;

  /// The tag the org already voted for, and the only valid merge destination
  /// in this group.
  Tag get keep => all.first;

  /// The tags whose records would move onto [keep].
  List<Tag> get merge => all.sublist(1);
}

/// Groups of tags that probably mean the same thing.
///
/// **Active tags only.** The list is fetched with `include_archived=true` so an
/// admin can see what has been turned off, but an archived tag is not offered
/// on new records and so cannot be splitting anyone's work: it is a former
/// duplicate, not a current one. Grouping over the full list also means the
/// banner never goes away, and that gets worse once merge is wired, because a
/// merge archives the tag it empties: the banner would come straight back
/// offering to merge a tag with no records left.
///
/// Two invariants come from that filter and `TagsMergeView` depends on both:
/// [TagDuplicateGroup.keep] is always active (the server refuses an archived
/// destination, since moving records onto a tag the page shows as "Off" reads
/// as data loss), and a group never contains one tag twice (the server refuses
/// a self-merge).
List<TagDuplicateGroup> duplicateTagGroups(List<Tag> tags) {
  final groups = <String, List<Tag>>{};
  for (final tag in tags.where((t) => t.isActive)) {
    groups.putIfAbsent(normalizeTagName(tag.name), () => []).add(tag);
  }
  return [
    for (final group in groups.values)
      if (group.length > 1) TagDuplicateGroup(sortedTags(group)),
  ];
}

/// The body for `POST /tags/`.
///
/// Only `name`. `org` comes from the JWT and `slug`, `usage` and the totals are
/// all computed server-side; sending any of them would be a client naming
/// facts the server owns. Colour is not offered here for the same reason the
/// web does not offer it: the model has eighteen named hues and this app
/// renders tags in its own palette, so a picker would be the only place in the
/// product those eighteen exist.
Map<String, dynamic> tagCreatePayload(String name) => {'name': name.trim()};

/// The body for `POST /tags/<id>/merge/`.
///
/// `into` is resolved inside the caller's org by `TagsMergeView`, which is the
/// check that matters: it arrives in a request body, so an unscoped lookup
/// there would let an admin stamp another tenant's tag onto their own records.
Map<String, dynamic> tagMergePayload(String intoId) => {'into': intoId};

/// What a new tag has to have before it is worth a round trip, or `null`.
///
/// A fast fail for the obvious mistake, not a control. `TagsListView.post`
/// strips and re-checks the name itself, and that is the authority.
String? validateTagName(String name) {
  if (name.trim().isEmpty) return 'Give the tag a name.';
  return null;
}

/// What turning a tag off actually does, in the words the confirm dialog needs.
///
/// `TagsDetailView.delete` flips `is_active` and leaves the row, and every
/// record's link to it, in place. Nothing hard-deletes a tag anywhere in that
/// file. Calling this Delete would promise a removal that does not happen, and
/// the count is the real one already on screen rather than a vague warning.
String tagArchiveExplanation(Tag tag) {
  final used = tag.used;
  if (used == 0) {
    return 'Nothing carries this tag. Turning it off stops it being offered '
        'on new records. You can turn it back on.';
  }
  return '$used ${used == 1 ? 'record keeps' : 'records keep'} this tag. '
      'Turning it off stops it being offered on new records, and keeps it on '
      'the ones that have it. You can turn it back on.';
}

/// What a merge does, for the confirm dialog.
///
/// Unlike turning a tag off, this cannot be undone by pressing the other
/// button. The source is archived rather than deleted, so its name survives and
/// can be turned back on, but the records have moved and nothing remembers
/// which ones came from where.
String tagMergeExplanation({required Tag from, required Tag into}) {
  final moving = from.used;
  return '$moving ${moving == 1 ? 'record moves' : 'records move'} from '
      '${from.name} to ${into.name}, and ${from.name} is turned off. Records '
      'already on ${into.name} are untouched. This cannot be undone by '
      'merging back.';
}
