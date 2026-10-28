# Answering leads

Calls, meetings, emails, WhatsApp messages and texts are logged on the record they were about, and
each lead's first response is measured from them. Notes stay comments; a logged call is an
**interaction**, a separate record with a kind, a direction and a time. The API is
[Interactions](../api/interactions.md).

## Logging on the web

The lead, contact and deal pages have **Log activity** beside the note box (on a deal, beside the
Activity heading). It takes:

- **What happened**: Call, Meeting, Email, WhatsApp or Text.
- **Outgoing or Incoming** (not asked for a meeting, which is always outgoing).
- **How a call went**: Connected, No answer, Voicemail, Busy or Wrong number. Optional.
- **Minutes**, for a call or a meeting, and a **Subject**, for an email or a meeting.
- **When**, read in the org's timezone. Empty means now; more than five minutes ahead is refused.
- **Note**, up to 10,000 characters.

On a lead or a contact, **Call** and **WhatsApp** in the header open the form already set to that
kind, as well as dialling or opening WhatsApp. **WhatsApp** links to `https://wa.me/<number>` and
appears only when the phone number could be read in international form (`phone_e164`).

The timeline lists interactions beside notes, files and the record's creation, newest first, with
a filter for Conversations, Notes and Files once there are two of them. An enquiry that arrived
through [lead capture](lead-capture.md) shows as "Enquiry via web form" (or webhook, or email) with
what the person sent. The person who logged an interaction, or an admin, can delete it; enquiries
cannot be deleted. A timeline shows only interactions on records the viewer can open.

## Logging on the phone

The phone app (1.14.0) logs what was just done. On a lead or a contact, **Call**, **Email**,
**Message** and **WhatsApp** open the dialler, the mail app, the messages app or WhatsApp, and when
the person comes back to BottleCRM a sheet asks "Log this call to Maria?" with:

- **How a call went**: Connected, No answer, Voicemail, Busy or Wrong number. Optional.
- **Duration**, for a call: the time spent away from the app, marked as estimated until it is
  changed. Android does not let Play Store apps read the call log, so the app cannot know how long
  the call really lasted.
- **Note**, and **Log** or **Skip**.

The sheet appears once per tap, only on the first return to the app and only within two hours of
the tap; a return later than that asks nothing, then or afterwards. If saving fails, the sheet stays
open with **Retry**. The interaction is logged as outgoing, at the time of the tap. **WhatsApp**
opens `https://wa.me/<number>` with "Hi Maria," typed in, and appears only when the number could be
read in international form. On a contact who asked not to be called, **Call** is shown as "Do not
call" and does nothing. The leads list's call and email icons offer the same sheet.

The lead, contact and deal timelines list interactions beside notes and files, and **Log activity**
on them records a call, meeting, email, WhatsApp or text done elsewhere, as outgoing and now. An
incoming interaction, an earlier time, a subject and deleting one are on the web.

## First response

A lead's **first response** is the earliest outgoing call, meeting, email, WhatsApp or text on the
lead, whoever or whatever logged it, never earlier than the lead's creation. A call nobody answered counts: an
attempt is a response. An incoming call does not, and changing the status does not. The fields are
worked out again from the stored interactions whenever one is logged, edited or deleted, so
deleting or backdating the first response moves it.

**Response time** is the first response minus the lead's creation, on the clock: nights and
weekends count. Leads created before the upgrade are left out of response times, so an old lead
does not report a wait of months. A logged outgoing interaction also moves the lead's **Last contacted** date.

## Unanswered leads

A lead is **unanswered** when it is assigned, in process or recycled and nothing outgoing has been
logged since it arrived or last enquired again. Leads that arrived before the organization had this
release are left out, so an old backlog does not fill the list; an old lead that enquires again
counts from that enquiry.

On the web:

- **Leads, Unanswered** lists them with the longest wait first, and the list's last column says
  how long each has waited. It combines with the other filters, such as an owner.
- **Today** shows a card with how many leads are waiting and the longest wait, over the leads the
  viewer can see.
- A lead that is waiting says so at the top of its page and in its Timeline panel, which also shows
  the first response time and who gave it.

On the phone, the leads list has an **Unanswered** filter, longest wait first, with "waiting 2 h 14
min" on each lead and its call icon one tap away; the home screen's Needs Attention box shows how
many leads are waiting and opens that list. The Response time view is on the web only.

An admin can also have the assignees reminded when a lead has waited 5, 15, 30 or 60 minutes: see
[The waiting reminder](lead-alerts.md#the-waiting-reminder).

## Response time view

**Leads, Response time** (`/leads/response-times`) shows, per person, over the last 7, 30 or 90
days: leads answered, the median time to first response, the share answered within 5 minutes and
within an hour, and how many leads assigned to them are waiting now. A lead counts for whoever
logged its first response. An admin sees every active member; a member sees only their own row.

## Outcomes view

**Leads, Outcomes**, beside Response time, shows per person over the last 7, 30 or 90 days: the
leads won and lost, the win rate, the median time from a lead's arrival to its win, and the new
leads that arrived. A lead is won when it is converted or moved to a won stage and lost when it is
closed or moved to a lost stage; a recycled lead is open again. Each lead counts for whoever is
assigned to it now, so a lead with two assignees counts for both. Win and loss dates are recorded
from the upgrade onward, since nothing recorded them before, and the view shows the date it
measures from. An admin sees every active member and the org's totals, counting each lead once; a
member sees only their own row. It is on the web only.

## Older servers

A web app newer than its API hides what the API cannot serve: with no interactions endpoint the
timeline shows notes and files only and the note box is the only way to record a call; with no
response fields the Unanswered view, the Response time view and the Today card do not appear.
The phone app does the same: with no interactions endpoint it shows no Log activity and no sheet
after a tap, and with no response fields it offers no Unanswered filter and no count.
