# BFS Material Orders

Web app for Building Finishing Services (BFS) staff to order materials for jobs.
Orders are emailed to the stores team and can be printed.

Built with **Django 5 (Python)** and **PostgreSQL**, hosted on **Render**.
Pages are plain server-rendered HTML with no separate front-end build.

> Status: **Stages 1–5** (everything needed for launch) are complete:
> sign-in, approval and roles; the catalogue import and admin editing;
> ordering with drafts, measure types, submit, amend and cancel; the order
> email to stores (print-friendly HTML) with retry and a printable page; and the
> daily data retention job.
>
> To deploy, follow [`docs/RENDER_SETUP.md`](docs/RENDER_SETUP.md).
>
> Decisions agreed with BFS during the build are recorded in
> [`docs/DECISIONS.md`](docs/DECISIONS.md).

---

## How the code is organised

| Folder | What it does |
|---|---|
| `bfs/` | Project settings, top-level URLs and the home page |
| `accounts/` | Users, sign-in, registration, approval, roles and password reset. Its users table is designed to be shared with a future job management system. |
| `audit/` | One shared audit history ("who did what, when") used by every module |
| `orders/` | Orders and order lines, pricing rules (`pricing.py`), order rules (`services.py`), linked-item and kit prompts (`suggestions.py`) and the ordering screens |
| `notifications/` | The order document (one template for the email and the print page), sending, and the failed-email list with retry |
| `retention/` | The daily retention job (`run_retention` command), its run log, and the admin Retention page |
| `catalogue/` | Material items, the nine sections, the spreadsheet import and its report, linked-item rules and kits |
| `templates/` | The HTML pages |
| `static/` | Stylesheet |
| `tests/` | Automated tests (run with `pytest`) |

### Roles and account status

- New accounts start as **Pending** and cannot sign in.
- An admin approves or rejects them under **Users** in the top bar. Admins get
  an email when someone registers, and the top bar shows how many are waiting.
- **Ordering user:** places and manages their own orders.
- **Admin:** everything an ordering user can do, plus user approval, catalogue
  management, all orders and reports. Admins can also open **Data admin**
  (Django's built-in admin screens) for direct record editing.
- Admins can **disable** an account, which signs the user out at once, and
  **re-enable** it later. Admins cannot disable or demote themselves.
- **Password reset:** an admin can click **Send password reset**, which emails the
  user a one-time link that is valid for 3 days. If the email fails, the link is
  shown to the admin so it can be passed on another way. Approved users can
  also use **Forgotten your password?** on the sign-in page.
- Passwords are stored hashed (PBKDF2). The minimum length is 10 characters.
- Approvals, rejections, role changes, disabling and reset links are written to
  the audit history (**Data admin > Audit entries**).

---

## The catalogue

Each item has: Part No. (the store code), the original catalogue name, a
display name, a section, a trade price, a measure type, a unit, a pack size,
and active, flammable, incomplete and "code to be confirmed" flags. Sell price,
stock levels and stores locations are never stored.

**Sections** (admin top bar > **Sections**): rename, reorder (the **Order**
number: lower comes first), add or remove sections. Tick **Flammable store** on
any section whose items should be flagged FLAMMABLE.
- **Subsections:** on a section's page, add, rename, reorder or remove subsections
  (one level, e.g. Electrical > Sockets and Switches). They appear as a second
  row of buttons on the order page. Removing a subsection keeps its items in the section.
- **Moving items:** in Catalogue, tick the items, choose **Move to section /
  subsection…** and press Go. You can also edit an item's Section/Subsection
  directly. Items moved by an admin stay put when the catalogue is re-imported.
- Renamed sections still match their old names on import.
- An import only deactivates items that came from the same file, wherever they have since been moved.
- **Removing a section never deletes its items.** They become *Unsectioned*,
  are listed under **Other** on the order page, and can be found in Catalogue
  with the *needs attention > No section* filter. Edit an item's **Section** to move it.
- Past orders keep the section name they were placed with.
- All changes are recorded in the audit history.

**Editing items:** go to **Catalogue** in the top bar. Use the **needs attention**
filter to find codes to confirm, incomplete items, garbled names, and packs
whose pack size is still 1. Click a Part No. to edit it. The display name can
also be changed directly in the list (press **Save** at the bottom). Every
change is recorded in the item's **Change history**.

- Changing a temporary Part No. (for example ELE118-B to ELE130) clears
  "code to be confirmed" automatically. You can also select items and choose
  **Mark codes as confirmed**.
- Items cannot be deleted, so past orders stay intact. Select them and choose
  **Deactivate selected items** instead.
- An item is **incomplete**, and cannot be ordered, while it has no name or no trade price.
- Measure types: *Each*, *Pack* (the price is per pack and the pack size is
  stored), *Area (m²)*, *Linear (m)* (not used yet), *Cut to order (m)* (needs
  the catalogue length the price covers, e.g. 3.0 for worktops), and
  *Whole items only*.

### How to import the catalogue

1. Go to **Import** in the top bar, choose the `.xlsx` or `.csv` file and press
   **Upload and preview**.
2. Read the report. **Nothing is saved yet.** It lists new items, updates,
   items that will be deactivated, temporary codes, incomplete rows, sell prices
   below trade, names that were repaired or still look garbled, and ignored columns.
3. Press **Apply import**, or **Discard**.

From the command line (the same rules apply):

```bash
python manage.py import_catalogue catalogue.xlsx            # preview only
python manage.py import_catalogue catalogue.xlsx --apply    # apply as well
```

**What the file needs:** a heading row with at least **Part No.** and
**Description**. Also read if present: **Trade Price**, **Sell Price**, **Unit**,
**Section**, **Flammable**. All other columns are ignored (Order, Est. Time,
Stock, Stores Location, supplier columns). The section comes from a Section
column, or from a heading row such as "Plumbing", or from the worksheet name.

**Import rules**

| Situation | What happens |
|---|---|
| Part No. appears more than once | The first keeps the code. Later ones become `-B`, `-C`… (e.g. `ELE118-B`), flagged *code to be confirmed* and listed in the report. |
| Placeholder code (e.g. `PLU0`) | Each one becomes `PLU0-A`, `PLU0-B`…, flagged *code to be confirmed*. |
| Missing name or trade price | Imported, flagged *incomplete*, listed in the report, and not orderable until completed. |
| Sell price below trade price | Listed for review only. Sell price is otherwise ignored. |
| Garbled characters (`âˆ’`, `Â²`…) | Repaired automatically (`-`, `²`). Names still garbled are flagged *check name*. |
| Item already exists | Original name, trade price and section are updated. **Display name, unit, pack size and measure type are never overwritten.** |
| Item no longer in the file | Deactivated (not deleted), but only for sections included in the file. Items added by an admin are never deactivated by an import. |
| Temporary code corrected by an admin | Still matched to its spreadsheet row on the next import, so it isn't duplicated. |

New items get starting values from the decisions recorded in the brief: worktops
and splashbacks are cut to order (3.0 m), vinyl and flooring priced per m² use area (cut to the exact length and width ordered),
"per 100" and similar become packs, PLU037 is a pack of 8, BFS05 a pack of 80,
PLA007/PLA012/PLA015 are whole items, and so on. The rules are in
`catalogue/setup_rules.py`.

---

## Ordering

- **Orders** has two tabs:
  - **My Orders:** your drafts and the orders you have placed.
  - **All Orders:** everyone's orders. Anyone can view them; only the owner or an admin can change them.
- **New order** opens the order page straight away. Add items first (browse
  by section, or search across all sections), then fill in the job details on
  the right: job number, property address, property type (Void/Occupied),
  requested delivery date (not in the past), the operative's name (required for occupied properties) and any special instructions.
  How you enter each item depends on its measure type:
  - **Each, pack and whole items:** a whole number (for packs, the number of packs). Adding the same item again increases its quantity.
  - **Area (vinyl and other flooring):** the exact length and width to be cut, in metres
    to 0.1 m. Stores cut to size. The app works out the m², with no waste allowance.
  - **Cut to order (worktops, splashbacks):** a length in metres, to 0.1 m.
  - **Non-stocked items:** typed in by name, unit and quantity. They are marked
    NON-STOCKED and have no price.
- **Drafts:** saved at any time and visible only to their owner. A draft is
  deleted 30 days after it was last saved. Users are warned at sign-in when a
  draft will be deleted within 5 days.
- **Submit:** the order gets the next number in the form `BFS-YYYY-NNNNNN` and
  an order date. Trade prices are fixed on the order at that moment.
- **One order per job:** a job number can have only one submitted order. Drafts
  and cancelled orders don't count. This is checked on submit and also
  enforced by the database.
- **Amend:** the owner or an admin can change a submitted order. Each change is
  recorded in the order's history. **Send amended order to stores** sends the
  updated order.
- **Cancel:** the owner or an admin. Recorded in the history. The job can then
  be ordered again.
- **Delete (admins):** for orders entered by mistake. A reason is required, and
  active orders are cancelled first so stores get a CANCELLED email.
- **Order value:** shown in the app only, never to stores.
  - Each, pack and whole items: quantity × trade price.
  - Area (vinyl and other flooring): m² (to 2 decimal places) × price per m², no waste allowance.
  - Worktops and splashbacks: length ÷ catalogue length × price (an estimate).
  - Each line is rounded to the penny. Non-stocked items are excluded.
  - The code is in `orders/pricing.py`.

## Linked items and kits

**Linked items** (admin top bar > **Linked items**): "when X is added, suggest Y".
- Each rule has a trigger item, one or more suggested items, and an optional
  note shown to the user (e.g. "Sealant needs an applicator gun").
- Tick **Suggest both ways** to make the rule two-way: adding a suggested item
  then also suggests the trigger item.
- As soon as the trigger item is added, the order panel shows "Commonly ordered
  with …", with **Add** buttons and a **No thanks** button.

**Kits** (admin top bar > **Kits**): a named set of items with default quantities,
e.g. a close-coupled toilet kit (pan, cistern, seat, pan connector, valves).
- Tick **Prompts kit** on the items that should offer the kit. Adding one of
  them then offers the rest of the kit as a tick list: quantities can be
  changed, and items already on the order are left unticked.
- Users can also add a whole kit from the **Kits** tab in the item browser,
  which shows an estimated kit value.
- Each kit item becomes a normal order line labelled **KIT: name**, in the app
  and in the stores email.

In both cases, items already on the order, inactive items and incomplete items
are never suggested. Nothing is added without the user pressing a button, and
submitting is never blocked.

## Order emails

- **Sent to `STORES_EMAIL`** (stores@bfsuk.org) from materialorders@bfsuk.org on
  submit, on **Send amended order to stores** (subject starts `AMENDED:`, or `AMENDED (NEW DATE):` when the delivery date changed), and on
  cancel (subject starts `CANCELLED:`). Amended emails show what changed since
  the last version: a summary box, lines marked NEW, CHANGED (with the old value)
  or REMOVED (do not supply), and changed job details highlighted.
- **Reply-To** is the person who placed the order, so a reply from stores goes to them.
- **Body:** a print-friendly HTML table, with items grouped under section headings in catalogue order. There is no PDF attachment: stores print the email itself.
  - Each item shows its Part No. and display name. Non-stocked items show NON-STOCKED.
  - Flammable items are marked.
  - Worktops show a cut instruction (e.g. "cut to 1.9m"), and flooring shows
    "Cut to L × W = A m²".
  - **No prices or totals.**
- **Print:** every order has a **Print** button showing the same content as the email.
  The template is `templates/notifications/order_document.html`.
- **If sending fails:** the order is still saved, and the user sees a warning.
  The send is listed under **Emails** in the admin top bar (with a red count),
  where an admin can **Retry**.
- **Checking the mail settings after deploying:** in the Render Shell, run
  `python manage.py sendtestemail you@bfsuk.org`.

---

## Data retention

The job runs every night as a Render cron job (`bfs-orders-retention`, 02:15 UTC).

- **Orders more than 30 days past their delivery date** (submitted or cancelled)
  are **deleted**, with their lines, history and email log.
- **Drafts not saved for 30 days are deleted.**
- **Every run is logged** with the order numbers it removed (no personal
  details). To see the log, go to **Retention** in the admin top bar.
- This applies only to this app's records. Stores keep their own copies and
  metrics under their own process.

**Running it by hand:**

- On the website: **Retention > Run now**.
- From the command line (locally, or from the web service's **Shell** tab in Render):

  ```bash
  python manage.py run_retention --dry-run   # show what would happen, change nothing
  python manage.py run_retention --manual    # run it now
  ```
- In the Render dashboard: open the `bfs-orders-retention` cron job and press **Trigger Run**.

---

## Local setup

You need Python 3.12 or newer and PostgreSQL 14 or newer.

```bash
# 1. Create a database
createuser bfs --pwprompt          # use password "bfs" for local work
createdb bfs --owner bfs

# 2. Install
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

# 3. Settings for local work
export DEBUG=1
export DATABASE_URL=postgres://bfs:bfs@localhost:5432/bfs

# 4. Create the tables and the first admin
python manage.py migrate
python manage.py createsuperuser   # asks for email, full name and password

# 5. Run
python manage.py runserver
```

Open http://localhost:8000. With `DEBUG=1`, emails are printed in the terminal
and not sent.

### Running the tests

```bash
pytest
```

The tests create and remove their own temporary database. The `bfs` database
user needs the `CREATEDB` permission (`ALTER USER bfs CREATEDB;`).

---

## Environment variables

| Name | Required | Purpose |
|---|---|---|
| `SECRET_KEY` | Yes (production) | Signs sessions and reset links. Render generates it. |
| `DATABASE_URL` | Yes | PostgreSQL connection string. Render fills it in from the database. |
| `DEBUG` | No | `1` for local development only. Never set it on Render. |
| `ALLOWED_HOSTS` | No | Extra host names, comma-separated (for example a custom domain). The Render host name is added automatically. |
| `EMAIL_HOST` | No | SMTP server. Default `smtp.ionos.co.uk`. |
| `EMAIL_PORT` | No | Default `587`. |
| `EMAIL_USE_TLS` | No | Default `1`. |
| `EMAIL_HOST_USER` | No | Default `materialorders@bfsuk.org`. |
| `EMAIL_HOST_PASSWORD` | Yes (production) | Password for the mailbox above. Set it only in the Render dashboard. |
| `DEFAULT_FROM_EMAIL` | No | Default `BFS Material Orders <materialorders@bfsuk.org>`. |
| `ORDER_VALUES_ENABLED` | No | Default off: prices and order values are hidden and don't block ordering. Set to `1` to show them. |
| `STORES_EMAIL` | No | Where orders are sent. Default `stores@bfsuk.org`. |
| `LOG_LEVEL` | No | Default `INFO`. |

Secrets are never stored in the code or in this repository.

---

## Server size

The web service runs one gunicorn worker with 4 threads, which is plenty for
about 20 users. This uses roughly 90 MB of the 512 MB Starter plan. The worker
is restarted every ~1000 requests so memory can't creep up. The settings are
in `startCommand` in `render.yaml`.

## Deploying to Render

A beginner's step-by-step guide is in [`docs/RENDER_SETUP.md`](docs/RENDER_SETUP.md). In short:

Render has no UK region, so the app uses **Frankfurt (EU)**.

1. In Render, choose **New > Blueprint** and select this repository. Render reads
   `render.yaml` and creates:
   - `bfs-orders`: the web service (Starter plan)
   - `bfs-orders-db`: PostgreSQL (Basic 256 MB plan)
   - `bfs-orders-retention`: the daily retention cron job
2. When asked, enter **`EMAIL_HOST_PASSWORD`** (the IONOS mailbox password).
3. Wait for the first deploy. Database migrations run automatically before each deploy.
4. Create the first admin. Open the web service's **Shell** tab and run:
   ```bash
   python manage.py createsuperuser
   ```
5. Sign in at `https://<your-service>.onrender.com`. Other staff use
   **Request an account**, and you approve them under **Users**.

Estimated cost: about $7/month for the web service plus about $6/month for the
database. The daily cron job adds a few cents per month, with a minimum charge of about $1.

**To be confirmed:** the region and plan once the Render account is set up (open item).

---

## Still to come (after launch)

- Print layout refinements (brief stage 6). Amendment and cancellation emails (stage 7) are already built.
- Admin reports on order value.
- An API for the future job management system.
