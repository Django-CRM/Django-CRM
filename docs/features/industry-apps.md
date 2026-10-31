# Industry apps

An industry app shapes an organization for one kind of business: the words it uses for leads and
deals, pipelines for leads, deals, tickets and tasks, custom fields, tags, lead sources and
products, and, if you want them, sample records to try things on. Four apps ship with BottleCRM:

| App id | Name | For |
| --- | --- | --- |
| `professional-services` | Professional services | Agencies, consultancies and accounting firms selling billable work. |
| `real-estate` | Real estate | Agencies and brokers selling and letting residential and commercial property. |
| `education-admissions` | Education admissions | Coaching centres, training institutes and colleges managing admissions. |
| `events-weddings` | Events & weddings | Wedding planners, event agencies and venues taking enquiries through to a delivered event. |

Each app is one of the manifests described in [Demo data and packs](../getting-started/demo-data.md),
and its manifest `version` is the app's version. What makes it an app is a record of the install,
kept per organization (`InstalledApp`, table `installed_app`): the version installed, who installed
it and when, what the app created and everything it has ever offered the org.

Only organization admins install, update and remove apps; any member can see which are installed.
On the web, apps are under Settings, then Apps, above the Modules section; the phone has the same
screen. New organizations can pick an app when they are created.

## Install

Installing first shows what the app will do, and does nothing until you confirm:

- **Wording**, such as "Deals called Engagements". An app sets only words the org has not set.
- **Fields, pipelines, tags, sources and products.** Anything the org already has, by name for
  pipelines and tags, by key for fields and sources and by SKU for products, is listed as kept.
  The app does not rename it, change it or add a second one, and it never becomes the app's.
- **Parts of a switched-off module** are listed as skipped: a ticket pipeline while Tickets is off,
  a deal pipeline while Pipeline is off, or products while both Pipeline and Invoices are off. See
  [Modules](../getting-started/modules.md).
- **Sample records.** Offered when the org has no sample records yet, and switched on by default
  only when the org has no real leads, contacts or accounts. Every sample record is marked as one,
  and Clear sample data removes them later.

New pipelines arrive beside the org's own and never become the default, so existing records stay
where they are. Installing never changes or deletes a record.

### Deal pipelines

An app can bring deal pipelines, each with its stages. Real estate (version 3) brings three:

| Pipeline | Stages |
| --- | --- |
| Sale | Viewing, Offer made, Offer accepted, Agreement, Completed (won), Fell through (lost) |
| Listing | Valuation, Listing agreed, On the market, Under offer, Sold (won), Withdrawn (lost) |
| Rental | Viewing, Application, Referencing, Let agreed (won), Fell through (lost) |

A deal pipeline is created, with all its stages, only when the org has no deal pipeline of that name,
ignoring case. One the org already has is kept exactly as it is, stages included, and never becomes
the app's. Each stage has a code, a label, a kind (open, won or lost) and, on an open stage, the
days a deal is expected to spend there before it starts to age; the open stages' days are what
[stage aging](../api/opportunities.md#stages) reads. None of them becomes the default pipeline, so
a deal that names no pipeline still lands in the org's own. While Pipeline is off they are skipped,
and an update adds them once it is back on. Removing the app keeps them and every deal in them.

### The sample project

Professional services (version 3) includes a sample [project](projects.md) in its sample records:
"Meridian audit fieldwork", time and materials, on the sample account and deal for Meridian Health
Partners, with an hourly rate, an hours budget and an amount budget, one task, and three stopped,
billable time entries logged as the admin who installs the app, so its budget burn shows from the
start. It comes only with a new install's sample records: an update never adds it, and no app
switches Projects on or off. While Projects is off the project, its task and its time are skipped,
and while Timesheet is off its time is; without an installing admin (a management command) its time
is skipped too.

Clear sample data deletes the sample time entries, then the task and the project. A real time
entry, task or invoice on the sample project survives with its link to the project cleared, as when
any project is deleted.

## Update

An app shows **Update** when updating would add something the org does not have: a part the app
has never offered this org before, or one it skipped for a module that has since been switched
on. A newer version that adds nothing the org lacks shows no Update. An update never changes
anything that exists: no label, option, stage or word is rewritten, an item the org deleted does
not come back, and no sample records come with it.

A stage new to a pipeline the org already has (lead, ticket, task or deal) is not added. An update,
or installing a removed app again, names it, so an admin can add it by hand where it fits; a stage
like that is not an update on its own.

## Remove

Removing an app hides what it added and keeps every record:

- its custom fields are switched off, which keeps every value already stored;
- its lead sources and products are made inactive, so pickers stop offering them, while records
  and invoice lines that use them keep them;
- each word it set goes back to the default, unless the org's wording has changed since;
- its pipelines, deal pipelines included, and its tags stay, because records live in them and carry
  them, and sample records stay until Clear sample data removes them.

Only what the app created is touched; anything it kept stays as it was. Install the app again and
everything removal switched off comes back on, and its words come back unless another app has set
them since. Anything the org deleted or switched off itself while the app was installed stays that
way.

## Several apps

More than one app can be installed. The first one's wording wins: a later app skips words already
set. When two installed apps offer the same field, source, product or word and the one that added
it is removed, it passes to the other instead of being switched off.

## Extensions

An installed Django app can register an extension for an app id with
`common.apps_catalogue.register_app_extension(app_id, extension)`, usually from its
`AppConfig.ready()`. The extension adds parts a manifest cannot describe. The catalogue calls it
after the app's own parts, inside the same lock and transaction, on install, update and removal, so
a failure in either undoes both. Its items join the preview with type `object`, or `field` for fields it adds to its own objects, and what it has
pending counts towards Update. An app id takes one extension, and an app without one behaves
exactly as this page describes. A caller that holds its own manifest and extension uses
`install_pack`, `upgrade_pack`, `remove_pack` and `plan_pack` in `common.apps_catalogue` and
`common.packs.planner`, which take them as arguments instead of an app id.

## Organizations that applied a pack before apps

An organization that applied a pack before apps existed has that app installed, at the version it
applied last, by whoever applied it. The app owns what its runs created and the org still has, so
removing it treats those parts as above. If the app now offers something the org lacks, it
shows Update.

## API

Every endpoint takes the org from the signed-in profile, never from the request. An app id is a
key of the catalogue, and anything else answers `404`.

### List the apps

`GET /api/apps/`, any member. The apps in catalogue order, each with this org's state:

```json
{
  "apps": [
    {
      "id": "real-estate",
      "name": "Real estate",
      "description": "Agencies and brokers selling and letting residential and commercial property.",
      "version": 3,
      "status": "installed",
      "installed_version": 3,
      "update_available": false,
      "skipped_modules": ["tickets"],
      "installed_at": "2026-10-24T10:15:00Z",
      "installed_by_details": {"id": "6f1c2d3e-...", "name": "Maria Lopez"}
    }
  ]
}
```

`status` is `not_installed`, `installed` or `removed`. `skipped_modules` lists the modules whose
parts an installed app skipped and has not added since. `update_available` is `true` exactly when
an update would add something, which is when `POST .../upgrade/` does not answer `409`.

### Preview

`GET /api/apps/<id>/preview/?action=install|upgrade|remove`, admins only. Writes nothing.

```json
{
  "action": "install",
  "items": [
    {"type": "wording", "target": "lead", "label": "Leads called Enquiries", "effect": "add"},
    {"type": "pipeline", "target": "lead", "label": "Sales", "effect": "add"},
    {"type": "tag", "target": null, "label": "Hot", "effect": "keep"}
  ],
  "sample_data": {"offered": true, "default_on": true, "records": 58}
}
```

`type` is `wording`, `object`, `field`, `pipeline`, `stage`, `tag`, `source` or `product`, and items
come in that order; `object` items, and `field` items with an empty target, come from an app's [extension](#extensions). `target` is the
record type the item belongs to (`lead`, `contact`, `account`, `deal`, `ticket`, `task`, `invoice`),
`null` for tags, sources and products, or `""` for the objects an extension adds and the fields of those objects. `effect` is one of:

| Effect | Meaning |
| --- | --- |
| `add` | The action creates it, or sets the word. |
| `keep` | It stays as it is: the org already has it, another installed app still uses it, or (on removal) records live in it. |
| `turn_off` | Removal switches it off. |
| `turn_on` | Installing a removed app switches it back on. |
| `restore_wording` | Removal puts the default word back. |
| `skipped_module` | Its module is off, so the action skips it. |
| `add_by_hand` | A stage new to a pipeline the org has; the update does not add it. |
| `restore` | Installing a removed app, or updating one, brings back an object its removal archived. |
| `conflict` | An object the org made uses one of the app's keys, so none of the app's objects is added. The row for the taken key names the org's object. |

`sample_data` says whether sample records are offered, whether they are on by default and how many
the app would create. An update or removal never offers them.

### Install, update, remove

| Endpoint | Body | Refused with `409` when |
| --- | --- | --- |
| `POST /api/apps/<id>/install/` | `{"sample_data": true}`; without it, the preview's default | the app is installed |
| `POST /api/apps/<id>/upgrade/` | none | the app is not installed, or an update would add nothing |
| `POST /api/apps/<id>/remove/` | none | the app is not installed |

Each answers `200` with `{"app": ...}`, the app as the list shows it. A member gets `403`, an
unknown id `404`, and a `sample_data` that is not a boolean `400`. Installing a removed app
installs it again. Install and update each write a pack application record, and all three write a
security audit row (`APP_INSTALLED`, `APP_UPGRADED`, `APP_REMOVED`; see
[Security audit log](../api/audit-log.md#app-changes)).

`POST /api/packs/<id>/apply/` installs the app with sample records and answers with the run's
report, as it did before apps. On an installed app it adds nothing and answers an empty report.
`GET /api/packs/` still lists the catalogue for the organization creation page.

The code is `backend/common/apps_catalogue.py` (the actions), `backend/common/packs/planner.py`
(the preview) and `backend/common/packs/applier.py` (what creates each part).
