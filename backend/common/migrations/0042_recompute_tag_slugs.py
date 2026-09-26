"""Recompute every tag's slug under `Tags.slug_for`, which keeps non-ASCII letters.

The old rule was Django's ASCII `slugify(name)`. It mapped every Japanese,
Cyrillic, Arabic or Hindi name to "" and mixed names to their ASCII remainder,
so an org could hold one such tag at most. The column does not change (50
characters); only the stored values do.

THE ORG LOOP IS NOT DECORATION

`tags` is in ORG_SCOPED_TABLES, so `get_enable_policy_sql` gave it `FORCE ROW
LEVEL SECURITY`: the table owner is filtered like any other role, so running
the migration as the owning role does not help. The policy matches
`NULLIF(current_setting('app.current_org', true), '')`, which is empty during a
migration, so an unscoped `Tags.objects.all()` returns nothing in production
while returning everything on a superuser dev database and on SQLite. That is
how `webforms/0002` shipped a backfill that did nothing. This walks orgs and
sets `app.current_org` for each, the shape `webforms/0002` and `common/0031`
document; `org` is not org-scoped, so the outer loop sees every org. The same
policy also governs the UPDATE, which is why it runs inside each org's context.

COLLISIONS

They should not happen. The new slug is at least as fine-grained as the old
one: the old slug is a function of the new one (`slugify(slug_for(name)) ==
slugify(name)`), checked for every code point on its own and between letters.
So two names that had different old slugs, which the unique key guarantees for
any two tags in one org, have different new slugs. The exception is four
non-ASCII whitespace characters (U+0085, U+1680, U+2028, U+2029): the old rule
deleted them and the new one treats them as a space, so "a<U+2028>b" and "a b"
had slugs "ab" and "a-b" and would now both be "a-b". A stored slug the old
rule did not produce (a row written with `.update()` or `bulk_create`) is not
covered by the argument either. So the check below runs anyway, and a clash or
a slug past the column's width stops the migration naming the org and the
tags. Nothing is merged or truncated: which tag keeps the name is a decision
for the org's admin (rename one, or merge them), not for a migration.

A name with no letter or digit (emoji or punctuation only) still maps to "".
At most one such tag exists per org, since they all shared "" before, and it
keeps working; the API and importers now refuse to create another.

REVERSE

A no-op. Once this has run an org can hold "日本" and "中国" side by side, and
the old rule maps both to "", so recomputing with it would violate the unique
key. Leaving the Unicode slugs in place on a rollback is harmless: the older
code recomputes a tag's slug whenever it saves one.
"""

from django.db import migrations

# The live rule, not a frozen copy: one function owns the tag slug, and a later
# change to it needs its own migration regardless.
from common.models import Tags as LiveTags


def recompute_tag_slugs(apps, schema_editor):
    connection = schema_editor.connection
    # RLS and `set_config` are Postgres-only; on SQLite the loop still runs
    # and only the context call is skipped.
    is_postgres = connection.vendor == "postgresql"

    Org = apps.get_model("common", "Org")
    Tags = apps.get_model("common", "Tags")
    slug_max = Tags._meta.get_field("slug").max_length

    def set_context(value):
        if not is_postgres:
            return
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_org', %s, false)", [value])

    try:
        for org_id in Org.objects.values_list("id", flat=True).iterator():
            set_context(str(org_id))
            rows = list(
                Tags.objects.filter(org_id=org_id).values_list("id", "name", "slug")
            )

            by_slug = {}
            changed = []
            problems = []
            for pk, name, old in rows:
                slug = LiveTags.slug_for(name)
                if len(slug) > slug_max:
                    problems.append(f"{name!r} needs a {len(slug)}-character slug")
                if slug in by_slug:
                    problems.append(
                        f"{by_slug[slug]!r} and {name!r} both become {slug!r}"
                    )
                by_slug.setdefault(slug, name)
                if slug != old:
                    changed.append((pk, slug))
            if problems:
                raise RuntimeError(
                    f"Tag slugs in org {org_id} cannot be recomputed without "
                    f"merging or truncating tags: {'; '.join(problems)}. Rename "
                    f"or merge them, then run the migration again."
                )

            # Two passes, so a new slug that equals another row's not yet
            # updated old one cannot trip the unique key midway. "~" never
            # appears in a slug_for result, so the placeholders cannot clash.
            for pk, _slug in changed:
                Tags.objects.filter(pk=pk).update(slug=f"~{pk}")
            for pk, slug in changed:
                Tags.objects.filter(pk=pk).update(slug=slug)
    finally:
        set_context("")


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0041_clear_stale_rls_force_flags"),
    ]

    operations = [
        migrations.RunPython(recompute_tag_slugs, migrations.RunPython.noop),
    ]
