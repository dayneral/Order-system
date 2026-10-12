# Voids Management — plan

The second module of the BFS system, alongside Material Orders. It replaces the
Excel Void Tracker (Voids/ECC, TEMP, ARK, Concierge Stations, Electrical, TEMP
STOCK, Completed tabs).

Status: **planning**. Decisions agreed with BFS are in the table at the end.

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

| Role | Can |
|---|---|
| Voids admin | Everything: settings, areas, trades, contracts, import |
| Manager | See and edit all voids, dashboards, digests |
| Supervisor | See all voids (defaults to their own areas), edit voids, log inspections |

## 2. Data model

**Contract**: ECC, TEMP, ARK, Concierge. They all follow the same workflow;
the contract is a filter, not a separate process.

**Area**: SE, SW, NE, NW… Each area has a list of postcode districts (EH16, EH17…)
and a **default supervisor**.

**Void**
- SIMPRO number(s) (the tracker has some, like "10584/10995", so a void can have
  several), contract, address, postcode, area
- Area is set automatically from the postcode, but can be changed by hand
- Supervisor: defaults to the area's supervisor, but can be changed by hand.
  The area and the supervisor are separate filters.
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

### Importing from the live tracker during testing and roll-out

**Yes, this can be done.** It runs as a nightly job, like the existing retention job:

1. The job reads the live workbook. It can get it in one of three ways:
   - **(a) Recommended:** directly from OneDrive/SharePoint, using the Microsoft
     Graph API (needs a one-off app registration in your Microsoft 365 tenant).
   - (b) From a shared link.
   - (c) As a fallback, an admin uploads the file on an **Import** page.
2. The importer reads each tab, matches rows by **SIMPRO number** (or by the
   address when the SIMPRO number is blank), and creates or updates the voids.
   It understands the cell text: "STC", "C", "Kev 25/09" (who and date) and
   "Issued 1 bed paint" (kept as a note).
3. **It reads cell colours as well**, not just the values: red text becomes
   Claimed, and orange, yellow or green fills become the overall status.
4. Blank, header and divider rows ("NEW SYSTEM") are skipped. Rows the importer
   can't understand go on an **import report** to check, and are never guessed at.
5. During the trial **the spreadsheet is the master**: each night's import
   overwrites the imported fields. Notes, inspections and photos added on the site
   are kept. At go-live the import is switched off and the site becomes the master.
   One-way only: the site never writes back to the spreadsheet.

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
- **Channels**: email to everyone. **WhatsApp** for supervisors, through the
  WhatsApp Business Cloud API (Meta) or Twilio. Things to know:
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
2. Importer and nightly job, run against a copy of the tracker, with an
   import report.
3. Excel-style grid with filters and tabs, then the void page.
4. Phone layout for the grid and the void page.
5. Email alerts (new void → area supervisor) and digests.
6. Dashboards.
7. WhatsApp channel.
8. Inspections with photos, and go-live (switch the import off).

## 8. Open questions

- Where is the live tracker stored (OneDrive, SharePoint, Teams, a local
  drive)? This decides import option (a), (b) or (c).
- The full list of areas, their postcodes and supervisors. The tracker header
  covers SE only so far.
- What does a plain **"C"** mean on the Concierge tab (complete?), and **"?"**?
- Should the Completed tab be imported once as history, which Stage 3 trends need?
- WhatsApp: is there a company number we can use, and is a small cost per
  message acceptable?

## Decisions

| Date | Topic | Decision |
|---|---|---|
| 2026-10-12 | Platform | New `voids` app in this project, sharing logins, audit and email |
| 2026-10-12 | Look | Excel-style grid to make adoption easier; cards on a phone |
| 2026-10-12 | Status colours | Orange = in progress, Yellow = complete with minor snagging, Green = fully complete; red text = claimed for payment; STC = still to have trades assigned |
| 2026-10-12 | Contracts | ECC, TEMP, ARK and Concierge all use the same workflow |
| 2026-10-12 | Access | Supervisors and managers only |
| 2026-10-12 | Notifications | Email first; WhatsApp for supervisors |
| 2026-10-12 | Areas | Supervisors have default areas, but area and supervisor are separate, editable fields and filters |
| 2026-10-12 | Target | The client-nominated hand-back date is the main target |
| 2026-10-12 | Trial import | Nightly one-way import from the live tracker; the spreadsheet stays the master until go-live |
