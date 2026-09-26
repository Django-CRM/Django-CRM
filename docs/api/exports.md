# CSV exports

Six lists download as a CSV file: leads, contacts, accounts, deals, tickets and invoices. Each is a
`GET` under the list's own root, served by a subclass of `RecordExportView`
(`backend/common/csv_export.py`). See [Conventions](conventions.md) for the query-string rules the
lists share and [Errors](errors.md) for the shapes a refusal comes back in.

| Endpoint | View | File name | Rows come from |
| --- | --- | --- | --- |
| `GET /api/leads/export/` | `LeadExportView` (`leads/views/export_views.py`) | `leads-YYYY-MM-DD.csv` | `lead_list_queryset` (`leads/views/lead_views.py`) |
| `GET /api/contacts/export/` | `ContactExportView` (`contacts/export_views.py`) | `contacts-YYYY-MM-DD.csv` | `contact_list_queryset` (`contacts/views.py`) |
| `GET /api/accounts/export/` | `AccountExportView` (`accounts/export_views.py`) | `accounts-YYYY-MM-DD.csv` | `account_list_queryset` (`accounts/views.py`) |
| `GET /api/opportunities/export/` | `DealExportView` (`opportunity/views/export_views.py`) | `deals-YYYY-MM-DD.csv` | `deal_list_queryset` (`opportunity/views/opportunity_views.py`) |
| `GET /api/cases/export/` | `CaseExportView` (`cases/export_views.py`) | `tickets-YYYY-MM-DD.csv` | `case_list_queryset` (`cases/views.py`) |
| `GET /api/invoices/export/` | `InvoiceExportView` (`invoices/export_views.py`) | `invoices-YYYY-MM-DD.csv` | `filter_invoices` over `visible_invoices_qs` (`invoices/api_views.py`) |

The date in the file name is the day in the org's timezone (`export_filename`).

## Who may export, and which rows

Any member of the org may export (`permission_classes = (IsAuthenticated, HasOrgContext)`), and
gets exactly the rows the matching list would show them for the same query string, every page of
it. That holds because each export's `get_queryset` calls the function its list view builds its
queryset with, the one named in the table above, and never filters on its own. So the read rule
that hides a colleague's lead from a member on `GET /api/leads/` hides it from their file too, and
a ticket the caller cannot open is not in `tickets-*.csv`.

For personal access tokens and the organization API key the export is part of the list's resource:
`GET /api/leads/export/` needs `leads:read`, the same scope as `GET /api/leads/`
(see [Tokens and API keys](tokens-and-api-keys.md#scopes)).

## Query parameters

An export takes its list's query parameters, with the list's meaning, and ignores paging: `limit`
and `offset` do not narrow a file, which always holds every matching row. For example,
`GET /api/cases/export/?status=New&status=Pending&priority=High` writes every high-priority ticket
in New or Pending that the caller can see. The parameters each list reads are listed on its own page
([Leads](leads.md#list-leads), [Contacts](contacts.md), [Accounts](accounts.md),
[Opportunities](opportunities.md), [Cases](cases.md), [Invoices and estimates](invoices.md)), and
are the same set a [saved view](saved-views.md) may hold.

Two lists answer in halves that a file does not have, so the half is named in the query instead:

- Leads: the list returns open and closed leads separately. `?open=true` exports only the open
  ones, which is what the web list shows; without it both halves are written.
- Accounts and contacts: `?is_active=true` or `?is_active=false` exports one half. Without it,
  active and inactive rows are both written.

A malformed filter value (an id that is not a UUID, a day that does not exist, a number that is not
a number) is a `400` naming the parameter, returned before any byte of the file:

```json
{"assigned_to": ["'x' is not a valid id."]}
```

## The response

A `200` is a streamed `text/csv; charset=utf-8` body with
`Content-Disposition: attachment; filename="<prefix>-<YYYY-MM-DD>.csv"`. Send `Accept: text/csv`
or no `Accept` at all; both are served (`CSV_RENDERERS`, `common/renderers.py`). The file:

- starts with a UTF-8 byte-order mark, so Excel reads accented names correctly;
- has one header row, then one row per record, in the list's own order;
- writes people as their email address and several values in one cell as a `; `-separated list
  (owners, tags, linked accounts);
- writes dates as `YYYY-MM-DD` and timestamps as ISO 8601 in the org's timezone;
- prefixes any text cell that starts with `=`, `+`, `-`, `@`, a tab or a carriage return (also
  after leading spaces) with `'`, so a record named `=HYPERLINK(...)` is text in a spreadsheet
  rather than a formula (`safe_cell`). Numbers the server formats are written as they are.

The body is generated while it streams, after the request's middleware has finished, so the view
sets the org's row-level security context and timezone again for exactly as long as it runs
(`csv_response`).

## Columns

| File | Columns |
| --- | --- |
| Leads | ID, First name, Last name, Company, Job title, Email, Phone, Status, Source, Rating, Industry, Amount, Currency, Probability, Close date, Last contacted, Next follow-up, City, Country, Assigned to, Tags, Created |
| Contacts | ID, First name, Last name, Email, Phone, Title, Department, Organization, Account, Linked accounts, City, State, Country, Do not call, Active, Assigned to, Tags, Created |
| Accounts | ID, Name, Industry, Email, Phone, Website, City, State, Country, Employees, Annual revenue, Currency, Active, Assigned to, Tags, Created |
| Deals | ID, Name, Account, Pipeline, Stage, Amount, Currency, Probability, Close date, Type, Lead source, Assigned to, Tags, Created |
| Tickets | ID, Subject, Status, Priority, Type, Account, Assigned to, Tags, Created, First response, Resolved, Closed on |
| Invoices | ID, Number, Title, Status, Account, Client name, Client email, Issue date, Due date, Total, Paid, Due, Currency, Assigned to, Created |

Choice fields are written as their labels (`In Process`, not `in process`), and a deal's stage as
the label of that stage in the deal's own pipeline. The column lists are the `columns` method of
each view; those are the source of truth if this table and a file ever disagree.
