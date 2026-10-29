# Lead alerts

New in django-crm 1.39.0. A member learns at once when a lead needs them: in the app's
notifications, by email as before, and on an Android phone. Everything here is in
`backend/leads/alerts.py`, `backend/common/push.py` and `backend/leads/tasks.py`; the API is in
[Leads](../api/leads.md#lead-alerts).

## What is sent, and to whom

| Alert | Who gets it | When |
| --- | --- | --- |
| **New lead assigned** | each member newly put on a lead | Someone else assigns them, on the web, the phone or the API; an automation rule assigns them; a web form's rotation picks them; or a lead webhook or lead address that has an owner creates the lead |
| **New lead** | the org's admins | A lead arrives through [lead capture](lead-capture.md) (a web form, a lead webhook or a lead address) and still has no active member on it a minute later |
| **Enquired again** | the lead's assignees, or for a converted lead the owners of the contact it became | A repeat enquiry by any capture route is merged into the lead, or recorded on that contact |
| **Waiting for a reply** | the lead's assignees, else the admins | The [waiting reminder](#the-waiting-reminder) |

- The person who made an assignment is never told about it, and saving a lead with the people
  already on it tells nobody. An automation rule is not a person: everyone it assigns is told,
  even the member whose edit set the rule off.
- A lead a member adds by hand is theirs, so it sends no **New lead** alert. The minute's wait
  lets a web form's rotation, or an automation rule that assigns new leads, put someone on it
  first: then only that person hears, with **New lead assigned**. A lead whose only owner cannot
  sign in (their user account is deactivated) counts as nobody's, and one deleted within the
  minute alerts no one.
- Inactive members and deactivated users are never told.
- From 1.42.0, a repeat enquiry on a converted lead goes to the active owners of the contact the
  lead became, since they own the relationship now, and to nobody on the old lead. It names and
  opens the contact. With nobody active on the contact, nobody is told, as for the
  enquired-again email; a converted lead whose contact cannot be found (it is matched by the
  lead's email address) tells the lead's assignees as before. A web form's submission email for
  a converted lead goes the same way: to the contact's owners, besides the form's own notify list,
  and it opens the contact.
- From 1.42.0, the enquired-again email and a web form's submission email also skip an owner whose
  user account is deactivated, as the alerts always have.
- Nothing is sent for a spreadsheet import, an industry app's sample data, `seed_data`, a merge of
  two leads, a team's members joining the leads the team is on, or an enquiry dated more than 24
  hours back. The assignment and enquired-again emails behave as they did before this release.

Each alert names the lead and its source and opens it. In the app its link is `/leads/<id>`
(`/contacts/<id>` for a converted lead's repeat enquiry, which names the contact); on a phone it
reads, for example:

- New lead assigned: Maria Lopez (Google ads)
- New lead: Maria Lopez (Google ads)
- Maria Lopez enquired again (Google ads)
- Maria Lopez is waiting for a reply

A lead with no source drops the part in brackets. The name is the lead's first and last name, else
its company, email or title; a contact's is its first and last name, else its email. The source is
always the lead's.

## Choosing how you hear

Each member has two switches on their profile, both on by default and separate in each org they
belong to:

- **Lead alerts by email**: the assignment email, the enquired-again email, and a web form's
  submission email when it comes to you because you are on the lead (or on the contact a converted
  lead became). A member the form itself names to be told of every submission still gets that
  email.
- **Lead alerts on the phone**: pushes.

The in-app notification is always written. Only the member can change their switches; nobody else
can, an admin included.

## The waiting reminder

An admin chooses **Remind the assignee when a lead waits more than** 5, 15, 30 or 60 minutes in
the organization settings; it is off until then. Every minute, a scheduled job finds each
[unanswered lead](answering-leads.md#unanswered-leads) whose latest enquiry (or creation, when it
has none) is that old, and tells its assignees, or the admins when nobody active is on it, with
**Waiting for a reply**.

- It reminds once per enquiry. A lead that enquires again after its reminder, still unanswered,
  is reminded again; one that is answered is not.
- It counts from the moment it is switched on: a lead already waiting then is left alone unless it
  enquires again, so switching it on with a backlog sends nothing. Changing the minutes later
  keeps that moment; switching it off and on again moves it.
- A lead whose latest enquiry is more than 24 hours old is left alone, so a scheduler that starts
  late, or after an outage, does not send a burst about old leads.
- A late or doubled run reminds nobody twice: each reminder claims the lead first.
- Nothing is sent while the Leads module is off.

The job needs Celery beat running (`celery -A crm beat`); see
[Email and Celery](../self-hosting/email-and-celery.md#running-a-worker). Restart beat after the
upgrade so it picks the new entry up.

## Push to phones

Pushes go through Firebase Cloud Messaging (FCM) to the Android app. iOS waits for an iOS release
of the app, which also needs an Apple push key.

The app registers its push token after a member signs in and picks an org, each time it starts,
and whenever FCM gives it a new one; it removes it before signing out or switching org. A token
belongs to one org and, inside it, to whoever signed in on that phone last.

- A member gets pushes on their five most recently registered phones in each org; registering
  another removes the one that registered least recently.
- A phone that has not registered for 14 days (how long a sign-in lasts unused) is removed the next
  time a push would go to it: it signed out, perhaps while offline, or was registered by someone
  who has since lost the account.
- When FCM reports a token as no longer registered (the app was uninstalled or the token
  replaced), the server deletes it.

A push that FCM refuses for a moment (`429` or a `5xx`) is tried again three times, waiting 30
seconds, then 1 minute, then 2 minutes, or as long as FCM asks. Pushes are sent by the Celery
worker.

A push carries the lead's (or contact's) name and the lead's source, so they pass through Google's
FCM service.

### Turning push on for a self-hosted server

A push reaches only a phone app built with the same Firebase project as the server's service
account. The Play Store app belongs to BottleCRM's own project, so a self-hosted server cannot push
to it: build the app with your own project.

1. Use the Firebase project whose `google-services.json` you build the app with (see
   [Release builds](../mobile/release-builds.md)), or create one.
2. In the Firebase console, open **Project settings, Cloud Messaging** and check that **Firebase
   Cloud Messaging API (V1)** is enabled.
3. In **Project settings, Service accounts**, choose **Generate new private key**. Put the JSON
   file on the server, outside the repository, readable only by the user the API and the Celery
   worker run as: it holds a private key.
4. Set `FCM_SERVICE_ACCOUNT_FILE` to its path for both the API and the Celery worker, and restart
   them ([Environment variables](../reference/environment-variables.md#phone-push)).
5. Build and install the phone app with that project's `google-services.json`.

Without the setting, or with a file that is missing or is not a service-account file, push is off:
nothing is queued for it, and in-app notifications and emails work as before.
