# Voids Management — plan

The second module of the BFS system, alongside Material Orders. It replaces the
Excel Void Tracker (Voids/ECC, TEMP, ARK, Concierge Stations, Electrical, TEMP
STOCK, Completed tabs).

Status: **Stage 1 built** (list, void pages, trade updates, import, export, module access). Decisions agreed with BFS are in the table at the end.

---

## 1. Where it lives

A new Django app, `voids/`, in this project. It shares:

- `accounts/`: the same logins and the same approval process. New roles are added.
- `audit/`: every change to a void is logged (who, what, when).
- `notifications/`: email sending, the failed-email list and retry.
- The theme and the Display options.

Material orders can be linked to a void. A void page lists its orders, and
**Order materials** opens a new order with the address and SIMPRO number already
filled in.

Access is limited to **supervisors and managers**. Subcontractors and operatives
get no access.

**Access to each module is separate.** Each user has a switch per module:
**Material Orders** and **Voids** (and any later modules). An admin sets these on
the user approval and edit screen.
- Someone can order materials without seeing Voids, or the other way round.
- Users only see menu links and the home page tiles for the modules they can use.
- Typing a Voids web address without access gives "no access".
- Existing users keep Material Orders and start without Voids.
- Each change of access is logged in the audit history.

| Role | Can |
|---|---|
| Voids admin | Everything: settings, areas, trades, contracts, import |
| Manager | See and edit all voids, dashboards, digests |
| Supervisor | See all voids (defaults to their own areas), edit voids, log inspections |

## 2. Data model

**Contract**: ECC, TEMP, ARK, Concierge. They all follow the same workflow;
the contract is a filter, not a separate process.

**Area**: the four geographical areas, **NE, NW, SE and SW**. Each postcode
district (EH1, EH2…) belongs to one area.

**Supervisor patch**: a list of postcode districts for each supervisor. A patch is
**not** the same as an area. With three supervisors, patches cross area
boundaries. Starting patches:

| Supervisor | Postcode districts |
|---|---|
| Stee L | EH1, 2, 8, 9, 13, 16, 17, 19, 20 |
| Martin T | EH3, 11, 12, 14, 28 |
| Andrew F | EH4, 5, 6, 7, 15, 29, 30 |

Admins edit both mappings on a settings page, so adding a fourth supervisor later
means editing a patch, not changing code. Any district not on a list (EH10, EH21…)
goes on a "no supervisor" list for an admin to assign.

**Trade supervisor**: a supervisor responsible for one trade in **every** area.
**Jamie** looks after **Electrical**. He is linked to any void with electrical
work or an electrical flag, whatever its postcode. He gets that void's alerts and
it appears in his "My voids" list.

**Void**
- SIMPRO number(s) (the tracker has some, like "10584/10995", so a void can have
  several), contract, address, postcode, area
- Area and supervisor are both set automatically from the postcode district,
  and both can be changed by hand. They are **separate fields and separate
  filters**, so the list can be sorted and filtered by geography or by supervisor.
- Electrical supervisor (Jamie), added automatically when electrical work is needed
- Access (Master Key, Concierge, keysafe…), date the keys were received
- **Client hand-back date**: nominated by the client, and the main target
- Expected completion, if different from the hand-back date (the system warns when
  it is later than the hand-back date)
- Overall status (below), flags (Electrical, Rewire, MI Switch, Composite door /
  windows, Issue), free-text notes
- Last inspection date: worked out from the inspections

**Trade task**: one per trade per void. The trades are a list admins can edit.
The starting list is Clear Out, Joinery, Composite Door / Windows, T71, T77,
Plumbing, Plasterer, Electrical, Glazing, Painting, Vinyl / Flooring and Sparkle
Clean.

Each trade task has:
- A status: Not required · **STC** (still to have a trade assigned) · Assigned ·
  In progress · Complete · **Claimed** (claimed for payment; the red writing in the
  spreadsheet)
- Who is doing it (an operative or subcontractor, picked from a list or typed in)
  and the booked date
- Notes. A trade can have more than one visit, like "Ben 23/09, Kyle 06/10".

**Inspection**: date, supervisor, notes and photos (stage 4), plus snags.
**Snag**: a description, its trade, and whether it is open or closed.

**Temp stock checklist** (TEMP contract only): PO, carpets/vinyl, stock sheet sent
to stores, furniture in property, final check, completed date, passed for
invoicing, value.

### Overall status (the row colour)

The status moves on by itself as the trades change, and can be set by hand when
needed.

| Status | Colour | Meaning |
|---|---|---|
| New: needs visit | Purple/grey | Created, not yet visited by the supervisor |
| STC | White | Trades still to be assigned |
| In progress | **Orange** | Trades assigned, work started |
| Snagging | **Yellow** | Works complete, minor snagging |
| Complete | **Green** | Fully completed |
| Handed back | Archived | Moves to the Completed list |

## 3. Stage 1: replace the spreadsheet

- **Grid view that looks like Excel**: frozen header and address column, the same
  column order as the tracker, row colours as above, and a coloured cell per trade.
  Clicking a cell edits it in place (status, who, date).
- Tabs across the top that work like the current sheets: All · ECC · TEMP · ARK ·
  Concierge · Electrical (any void with an electrical flag) · Hand back this week ·
  Completed.
- Filters: area, supervisor, contract, status, flag, "trade outstanding = X",
  hand-back date overdue or within the next 7 days, and search by address,
  postcode or SIMPRO number. Each user's filters are remembered.
- A void detail page with the trades, inspections, snags, linked material orders
  and the history.
- Export the current view to Excel.
- Admin pages for areas (postcodes and supervisor), trades, contracts and
  operatives/subcontractors.

### Importing the tracker

The live tracker is on OneDrive. **No nightly sync is needed**: the copy we
have is enough to test with. Before launch BFS uploads the latest file once, and
from then on the site is the master.

- An admin uploads the workbook on an **Import** page. Every upload can first be
  run as a **trial** (shows what would change, saves nothing), then confirmed.
- Rows are matched by **SIMPRO number** (or by the address when the SIMPRO number
  is blank). Re-importing updates them and never creates a duplicate, so the
  final pre-launch upload can safely be run over the test data.
- It understands the cell text: "STC", "Kev 25/09" (who and date) and
  "Issued 1 bed paint" (kept as a note).
- **It reads cell colours as well**, not just the values: red text becomes
  Claimed, and orange, yellow or green fills become the overall status.
- Blank, header and divider rows ("NEW SYSTEM") are skipped. Concierge "C" and
  "?" values come from early versions of the sheet and are ignored.
- Rows the importer can't understand go on an **import report** to check, and are
  never guessed at.

### Completed tab (history)

The Completed tab is imported once as history, for the Stage 3 trends. The data
is patchy, and the columns moved over time, so:

- **Columns are found by their header name**, not by position. Names are
  matched loosely ("Vinyl / Flooring", "Flooring", "Vinyl"). Where a block of rows
  has its own header row, that header is used for the rows below it.
- Anything that can't be matched goes into the void's notes, so nothing is lost.
- Each historic void is marked **Imported – history**, with a quality flag for
  what is missing (no dates, no supervisor…).
- Dashboards **leave incomplete records out of a measure** instead of counting
  them as zero. For example, a void with no hand-back date is left out of the
  "on time" percentage, and the dashboard shows how many records each figure
  is based on.

## 4. Stage 2: automatic updates

- **New void in your area**: the area's supervisor is alerted straight away
  ("New void: 38 Balmwell Terrace, EH16 6PR, SE, SIMPRO 7968, hand back 24/10,
  needs visit"), with a link. It stays flagged *Needs visit* until the first
  inspection is logged.
- Other alerts: a trade overdue, expected completion later than the hand-back
  date, hand-back within 3 days with open trades, snagging raised, works complete
  ready for inspection.
- **Daily digest for each supervisor**: their voids due this week, overdue items,
  STC trades.
- **Weekly summary for managers**: all areas, the hand-backs due, what was missed
  and why.
- **Channels**: email to everyone. **WhatsApp** messages go to each supervisor's
  own WhatsApp, on their personal number. This is outgoing only: replies are not
  read by the site. It uses the official WhatsApp Business Cloud API (Meta).
  - **Testing:** Meta gives a free **test sender number** that can send to up to
    5 verified phone numbers. That covers you and the supervisors, so it needs no
    cost and no number of our own.
  - **Live:** one of the BFS WhatsApp numbers becomes the sender. A number
    registered with the API **can no longer be used in the normal WhatsApp app**,
    so use a spare or dedicated number, not a personal phone.
  - Unofficial tools that send from a personal account break WhatsApp's terms,
    and the number can be banned, so we won't use them.
  Other things to know:
  - It needs a business phone number, plus message templates that Meta approves.
  - It costs a small amount per message.
  - Each supervisor chooses email, WhatsApp or both in their profile.
  - Until WhatsApp is set up, email is used.
- During the trial, alerts can be switched off so that the nightly import doesn't
  set off "new void" alerts for every row.

## 5. Stage 3: dashboards

- Live voids count by area, contract and status.
- **Hand-back performance**: the percentage handed back on or before the client
  date, by month, area, supervisor and contract.
- Average days from keys received to hand-back, and from created to first visit.
- Bottlenecks: how many voids are waiting on each trade, and for how long.
- Subcontractor and operative workload and on-time rates.
- Cost per void from linked material orders (needs order values switched back on;
  see DECISIONS.md).
- Charts can be filtered and exported.

## 6. Stage 4: phone-friendly site use

- The grid turns into **cards** on a phone: one void per card, with trade chips.
- "My voids today" view for supervisors, with tap-to-call/navigate on the address.
- Inspection on site: set each trade's status, add snags, **take photos** with
  the camera, and sign off.
- Large controls with the same dyslexia-friendly display options.
- Later, if needed: install it to the home screen (PWA).

## 7. Build order

1. Models, admin pages for areas, trades and contracts, roles.
2. Importer (upload, trial run, report), including the Completed history.
3. Excel-style grid with filters and tabs, then the void page.
4. Phone layout for the grid and the void page.
5. Email alerts (new void → area supervisor) and digests.
6. Dashboards.
7. WhatsApp channel.
8. Inspections with photos, then go-live: final tracker upload and the site
   becomes the master.

## 8. Open questions

- Which postcode districts are in NE, NW, SE and SW? The importer can suggest a
  mapping from the "Void Area" column in the tracker for you to check.
- Jamie's surname, and email/mobile for each supervisor and manager.
- Whose phone numbers go on the WhatsApp test list (up to 5).
- A copy of the Completed tab when we reach the history import (a ZIP is fine
  if the file is large).

## Decisions

| Date | Topic | Decision |
|---|---|---|
| 2026-10-12 | Platform | New `voids` app in this project, sharing logins, audit and email |
| 2026-10-12 | Look | Excel-style grid to make adoption easier; cards on a phone |
| 2026-10-12 | Status colours | Orange = in progress, Yellow = complete with minor snagging, Green = fully complete; red text = claimed for payment; STC = still to have trades assigned |
| 2026-10-12 | Contracts | ECC, TEMP, ARK and Concierge all use the same workflow |
| 2026-10-12 | Access | Supervisors and managers only |
| 2026-10-12 | Module access | Each module is switched on per user by an admin; ordering doesn't give access to Voids |
| 2026-10-12 | Notifications | Email first; WhatsApp for supervisors |
| 2026-10-12 | Areas | Supervisors have default areas, but area and supervisor are separate, editable fields and filters |
| 2026-10-12 | Target | The client-nominated hand-back date is the main target |
| 2026-10-12 | Import | No nightly sync. Test with the current copy; one final upload before launch; matched by SIMPRO number so re-imports don't duplicate |
| 2026-10-12 | Areas vs patches | Four geographical areas (NE/NW/SE/SW) and separate supervisor patches by postcode district; both are filters |
| 2026-10-12 | Electrical | Jamie supervises all electrical work, in every area |
| 2026-10-12 | Concierge C / ? | Early-version values; ignored |
| 2026-10-12 | History | Completed tab imported once; columns matched by header; incomplete records are flagged and left out of dashboard figures they can't support |
| 2026-10-12 | WhatsApp (superseded) | Outgoing to supervisors' own WhatsApp via the official API; Meta test number for testing; a dedicated BFS number when live |
| 2026-10-12 | Notifications (revised) | **Email only.** WhatsApp is dropped |
| 2026-10-12 | Tabs imported | Only the **Voids** and **TEMP** tabs for now (Job type "Void" and "Temp"). Concierge, ARK, Electrical and TEMP STOCK are ignored |
| 2026-10-12 | Job types | One list for every job, with a **Job type** label (Void, Temp, Concierge Station; admins can add more). Job type is a filter (quick buttons on the list), not a separate tab |
| 2026-10-12 | Claimed | "Claimed" (red writing) is stored as a tick on each trade, separate from its status, because the tracker shows red writing on work in progress as well as complete work |
| 2026-10-12 | Supervisor details | Emails and phone numbers to be added later by BFS under Voids settings → Supervisors |
| 2026-10-12 | List layout | **Tiles** by default instead of a spreadsheet grid: area buttons (All properties, NE, NW, SE, SW) with counts, job type buttons, then one tile per property with a coloured circle per trade and a progress bar. Tap a tile to open it and update trades. The spreadsheet-style **Table** view is kept as an option |
| 2026-10-12 | Job diary | Supervisors add dated updates to a void (General, Site visit, Issue, Client contact, Snag). Trade and status changes add automatic lines. Authors can edit their own update for 15 minutes; admins can hide (not delete) one. A Site visit sets the last inspection date and clears "needs visit". The newest update shows on the tile |
| 2026-10-12 | Needs an update | Each void has a "last updated" time, moved by diary updates, trade changes and status changes (not imports or small detail edits). Open voids are flagged **amber after 5 days and red after 10** (editable: see Warning thresholds). Filter "No update in 7+ days" and sort "Least recently updated". Voids **on hold** are not flagged |
| 2026-10-12 | Key info | The old Notes box becomes **Key info** (key safe numbers, warnings), shown in a yellow warning box on the tile and at the top of the void page |
| 2026-10-12 | Photos | Not needed; managed on a separate system |
| 2026-10-12 | Concierge stations | Recorded as voids; the "Concierge Station" job type is removed (if no voids use it) |
| 2026-10-12 | Works start date | New **Works start** date on a void (not allowed to start before, or booked to start on), shown on the tile |
| 2026-10-12 | Trade statuses | **Assigned is merged into In progress.** A trade added to a job starts as **STC** until someone is assigned. Trade statuses: STC, Booked / ordered, In progress, Snagging / follow on, Complete, Not required |
| 2026-10-12 | Issues | **Issue belongs to the whole job**, not a trade: a red ISSUE banner on the tile and void page, with details. Raised by an Issue diary update (or the edit page), cleared with **Clear issue**. Trade issues from earlier imports moved to the job |
| 2026-10-12 | Tiles | Closed tiles are all the same size; an opened tile takes the full row. **Expected completion** is the large date on the tile, with hand-back and works start beside it. Opened tiles list the trades on the job first, then **Add other trade** |
| 2026-10-12 | Sorting | Sort menu on the list: **job number (lowest first, the default)**, hand-back date, expected completion, works start, least/most recently updated, address |
| 2026-10-12 | Electrical quick filter | **⚡ Electrical works** button beside the job types: voids with an electrical flag or Electrical on the job |
| 2026-10-12 | Opened tile (desktop) | Opens on its own **full row** with everything visible and no scrolling inside it: the summary on the left, trades in the middle, updates on the right. The following tiles close up the gap above it so no space is left. The opened tile is marked **Selected** with a teal border and tinted summary. Phones show the same content stacked |
| 2026-10-12 | Tile colour strip | Removed: the status label on the tile is enough. Only issues (red) and overdue updates keep a coloured outline |
| 2026-10-12 | Warning thresholds | Editable by admins under **Admin → Voids warning thresholds**: needs-an-update amber days (default 5) and red days (10), the "No update in … days" filter (7) and hand-back "due soon" days (7). Replaces the `VOIDS_STALE_*` settings |
