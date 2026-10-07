# BFS Material Orders

Web app for Building Finishing Services (BFS) staff to order materials for jobs.
Orders are emailed to the stores team and can be printed.

Built with **Django 5 (Python)** and **PostgreSQL**, hosted on **Render**.
Pages are plain server-rendered HTML with no separate front-end build.

> Status: **Stage 1** (sign-in, account approval and roles) is complete.
> The catalogue, ordering, email and retention stages follow.

---

## How the code is organised

| Folder | What it does |
|---|---|
| `bfs/` | Project settings, top-level URLs and the home page |
| `accounts/` | Users, sign-in, registration, approval, roles and password reset. Its users table is designed to be shared with a future job management system. |
| `audit/` | One shared audit history ("who did what, when") used by every module |
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
| `STORES_EMAIL` | No | Where orders are sent. Default `stores@bfsuk.org`. |
| `LOG_LEVEL` | No | Default `INFO`. |

Secrets are never stored in the code or in this repository.

---

## Deploying to Render

Render has no UK region, so the app uses **Frankfurt (EU)**.

1. In Render, choose **New > Blueprint** and select this repository. Render reads
   `render.yaml` and creates:
   - `bfs-orders`: the web service (Starter plan)
   - `bfs-orders-db`: PostgreSQL (Basic 256 MB plan)
2. When asked, enter **`EMAIL_HOST_PASSWORD`** (the IONOS mailbox password).
3. Wait for the first deploy. Database migrations run automatically before each deploy.
4. Create the first admin. Open the web service's **Shell** tab and run:
   ```bash
   python manage.py createsuperuser
   ```
5. Sign in at `https://<your-service>.onrender.com`. Other staff use
   **Request an account**, and you approve them under **Users**.

Estimated cost: about $7/month for the web service plus about $6/month for the
database. The daily cron job (stage 5) adds a few cents.

**To be confirmed:** the region and plan once the Render account is set up (open item).

---

## Still to come

- Stage 2: catalogue import and admin editing
- Stage 3: ordering, drafts and measure types
- Stage 4: order email with PDF
- Stage 5: 30-day retention job, run daily by a Render cron job
