# Private apps

A private app is your own app for BottleCRM, written as one JSON file. It can add custom objects
with their fields and links, fields on leads, contacts and the other built-in records, pipelines,
tags, lead sources, products and wording, and it can ask for a key so another service of yours can
read and write the org's records. An agency can install the same file into each client's org; a
company's own developer can package their setup and keep it in step.

Private apps are available on the hosted app. A self-hosted install does not
have them. They are for apps you wrote or were sent by someone you trust: BottleCRM does not
review them.

## Install an app

Only organization admins install, update, replace the key of or remove a private app. Open Settings,
then Apps, and find **Your own apps** under the industry apps.

1. **Upload the file**, or paste its text, and choose Upload. Nothing changes in the org yet. The
   file is checked and kept, and you are taken to a review screen. A file with a mistake is refused
   with the path of each problem, such as `objects[1].fields[3].field_type`.
2. **Read the review screen.** It says whether this is an install, an update from an earlier
   version, or an install again, and it always warns that BottleCRM has not reviewed the app and
   that you should install it only if you trust whoever sent you the file. It then lists:
    - **What it adds**, grouped by kind (objects and their fields, fields on built-in records,
      pipelines, tags, lead sources, products and wording). Each item says whether it will be
      added, is already there and kept, or conflicts with something you have.
    - **Custom objects**, when the app needs new places: how many it needs, how many your plan
      includes and how many are used.
    - **What it may access**: in plain words, which records the app's key may read and which it may
      write. Object access covers every custom object in the org, not only the app's own. If the app
      asks for event delivery, it receives events about the records it may read. The key is shown
      once and can write records but never settings. An app that asks for no access gets no key.
3. **Install.** An app that asks for access shows a checkbox, "I trust whoever wrote this app",
   which you tick first. The button is disabled, with the reason shown, when anything blocks the
   install: a clash with an object key you already use, a link to a module that is switched off,
   no room left in your plan's custom objects, or no free connected app place. A conflict blocks the
   whole app; it never installs a part of it.
4. **Copy the key**, if the app has one. It is shown once. You can replace it later.

Installing only adds. It never changes or deletes anything the org already has, and what you
approved on the review screen is exactly what is installed: the screen is built from the stored
upload, not from the file you chose again.

Installing a private app is open to every plan. The objects it adds count toward your plan's custom
objects as objects you create yourself do, archived ones included, so an app with objects needs
custom objects on the plan. An app with no objects does not.

## Update an app

Upload a file with the same `id` and a higher `version`. The review screen is the same, with the
access changes marked: scopes the new version adds are marked new and ones it drops are marked
removed. Confirming sets the app's key to exactly the new list of scopes, so the same key keeps
working. If the app asked for no access before and asks for some now, a key is created and shown
once. If it now asks for none, its key stops working.

An update adds what is new and the org does not have. It never changes or deletes an object, field,
pipeline or word the org already has. A field whose definition changed in the file (a new type or
new options) is listed as kept as it is, so you can see the change was not applied. A part the new
version drops stays in the org. Parts the app offers for a module that was switched off when it was
installed are added by uploading a higher version once the module is back on.

A version equal to or lower than the installed one is refused. Uploading the very same file again
returns the earlier upload instead of making a second one.

## Replace the key

Replace key on the app's row makes a new key and stops the old one at once. The new key is shown
once. An app's key is also listed in Settings, then Connected apps, with a note that the app
installed it; to disconnect it, remove the app.

## Remove an app

Remove on the app's row:

- switches off the fields it added to built-in records, makes its lead sources and products inactive
  and puts back the words it changed, as removing an [industry app](industry-apps.md) does;
- archives its custom objects and keeps every record in them (they still count toward your plan's
  custom objects until you delete them);
- stops its key at once and deletes its event subscriptions.

The app stays in the list as Removed. Install again applies the last installed version: the objects
come back with their records, and a new key is made if the app asks for access. The old key still
does not work.

## Write an app

An app is one JSON object in a UTF-8 text file of at most 200 KB. Duplicate keys within an object
are refused. A leading byte-order mark, Windows line endings and surrounding whitespace are fine.
The file is checked in full when you upload it: its size first, then every value in every section,
for its type and for the length or range BottleCRM can store. If any part is refused, nothing is
stored or installed, and every refusal names its path.

### Sections

Only these top-level keys are accepted; any other key is refused.

| Key | Required | What it holds |
| --- | --- | --- |
| `id` | yes | The app's name across versions. See the rules below. |
| `version` | yes | A whole number from 1 to 2147483647. |
| `name` | yes | Text, 1 to 60 characters. |
| `description` | no | Text, at most 500 characters. |
| `terminology` | no | Words to relabel, as in a [pack](../getting-started/demo-data.md#vertical-packs). |
| `lead_pipeline`, `case_pipeline`, `task_pipeline` | no | One pipeline each, with its stages. |
| `deal_pipelines` | no | A list of deal pipelines with their stages, at most 10. |
| `custom_fields` | no | Fields on built-in records, at most 50. |
| `tags` | no | At most 50. |
| `lead_sources` | no | At most 20. |
| `products` | no | At most 200. |
| `objects` | no | Custom objects, at most 20. |
| `access` | no | The scopes the app's key asks for. |

The sections from `terminology` to `products` are written exactly as they are in the packs behind
the [industry apps](industry-apps.md); the manifests in `backend/common/packs/manifests/` show
every one. A private app has no sample records: `sample_data` and `sample_dates_written_on` are
refused.

### The id and the version

The `id` is 1 to 30 lowercase letters, digits and hyphens, starting with a letter
(`^[a-z][a-z0-9-]{0,29}$`). BottleCRM records it as `x-<id>`, so it never clashes with one of
BottleCRM's own apps, and an id that equals a BottleCRM app's id is still your own app. Uploading a
file whose `id` matches an app the org has is an update of that app, and its `version` must be
higher than the installed one.

### objects

Each entry describes one custom object.

| Key | Required | Rules |
| --- | --- | --- |
| `key` | yes | 2 to 28 lowercase letters, digits and underscores, starting with a letter. Not a reserved word. Unique in the file. |
| `label` | yes | Text, at most 64 characters. |
| `label_plural` | yes | Text, at most 64 characters. |
| `name_label` | no | What the record's name is called, at most 64 characters. |
| `record_access` | no | `own` or `everyone`. |
| `board_field` | no | The `key` of one of this object's dropdown fields, which groups the object's board. |
| `fields` | no | A list of fields. |
| `links` | no | A list of links. |

An object has at most 100 fields and links together. If the org already uses an object key, the
install is blocked.

A **field** has these keys and no others:

| Key | Required | Rules |
| --- | --- | --- |
| `key` | yes | 1 to 64 lowercase letters, digits and underscores, starting with a letter. Unique among the object's fields and links. |
| `label` | yes | Text, at most 64 characters. |
| `field_type` | yes | `text`, `textarea`, `number`, `dropdown`, `date` or `checkbox`. Links go under `links`. |
| `options` | for `dropdown` | A list of `{"value": ..., "label": ...}`, at most 100, each value and label at most 255 characters. |

A **link** has these keys and no others:

| Key | Required | Rules |
| --- | --- | --- |
| `key` | yes | As a field key. |
| `label` | yes | Text, at most 64 characters. |
| `target` | yes | What it links to: `lead`, `contact`, `account`, `deal`, `ticket` or `task`, or `obj:<key>` for another object in the same file. Never an object the org built itself. |
| `many` | yes | `true` if a record can link to several, `false` for one. |

A link to a module the org has switched off blocks the install, with the reason shown.

### access

A list of scopes, each from this list and none repeated:

`leads:read`, `leads:write`, `contacts:read`, `contacts:write`, `accounts:read`, `accounts:write`,
`opportunities:read`, `opportunities:write`, `tasks:read`, `tasks:write`, `cases:read`,
`cases:write`, `invoices:read`, `invoices:write`, `interactions:read`, `interactions:write`,
`objects:read`, `objects:write`, `users:read`, `tags:read`, `hooks:read`, `hooks:write`.

These are the scopes the Connected apps screen in Settings offers. A scope outside the list
is refused. An empty or missing `access` makes a structure-only app with no key. The key can write
records but can never change an object's shape or any setting, and it can never install an app.

An app's key subscribes itself to events through the API; the file holds no event list. A
subscription needs read access to what its event carries, so `access` is also the limit on what the
app can hear.

### Limits

| What | Limit |
| --- | --- |
| The file | 200 KB |
| Objects | 20 |
| Fields and links on one object | 100 |
| Fields on built-in records | 50 |
| Tags | 50 |
| Products | 200 |
| Lead sources | 20 |
| Deal pipelines | 10 |
| New uploads | 20 a day per org |
| Versions of one app waiting above the installed one | 50 |

### Example

This is a complete file. It adds a roof type field on leads, a Site survey object with a status
board and a link to a lead, and a key that reads leads, reads and writes objects and manages event
subscriptions.

```json
{
  "id": "solar-installs",
  "version": 1,
  "name": "Solar installs",
  "description": "Site surveys and installations.",
  "custom_fields": [
    {
      "target": "Lead",
      "key": "roof_type",
      "label": "Roof type",
      "field_type": "dropdown",
      "options": [
        {"value": "flat", "label": "Flat"},
        {"value": "pitched", "label": "Pitched"}
      ]
    }
  ],
  "objects": [
    {
      "key": "site_survey",
      "label": "Site survey",
      "label_plural": "Site surveys",
      "name_label": "Address",
      "record_access": "everyone",
      "board_field": "status",
      "fields": [
        {
          "key": "status",
          "label": "Status",
          "field_type": "dropdown",
          "options": [
            {"value": "booked", "label": "Booked"},
            {"value": "done", "label": "Done"}
          ]
        },
        {"key": "roof_area", "label": "Roof area", "field_type": "number"}
      ],
      "links": [
        {"key": "lead", "label": "Lead", "target": "lead", "many": false}
      ]
    }
  ],
  "access": ["leads:read", "objects:read", "objects:write", "hooks:write"]
}
```

### What an upgrade does not change

Writing a new version of this file does not edit an object, field, pipeline, tag or word the org
already has, even if you change its definition in the file. To change something that is installed,
the org's admin changes it in Settings. New objects, new fields and the other new parts arrive,
and the access list is applied as written.
