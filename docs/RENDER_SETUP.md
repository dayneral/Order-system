# Setting up Render: step by step

This guide assumes you have never used Render. It takes about 30 minutes.
You need: the GitHub login that owns `dayneral/Order-system`, a payment card,
and (for live emails) the IONOS mailbox password.

Render reads the file `render.yaml` in this repository and creates everything
automatically:

| Name in Render | What it is | Plan | Approx. cost |
|---|---|---|---|
| `bfs-orders` | The website | Starter | $7/month |
| `bfs-orders-db` | The PostgreSQL database | Basic 256 MB | $6/month |
| `bfs-orders-retention` | The nightly data retention job | Starter (billed per minute) | about $1/month |

Everything is in **Frankfurt (EU)**. Render has no UK region, and the region
cannot be changed after the services are created.

---

## Before you start: put the code on the main branch

Render deploys one branch. The code is currently on the branch
`claude/bfs-materials-ordering-app-acn9un`. The simplest option is to merge it
into `main` first (ask Claude to open a pull request, then press **Merge** on
GitHub). Alternatively, choose that branch in step 3.

---

## 1. Create a Render account

1. Go to <https://render.com> and choose **Get Started**.
2. Sign up with **GitHub**. This links the two accounts.
3. Under **Account Settings > Billing**, add a payment card. The plans used here
   are paid, and the free database is deleted after 30 days, so it is not suitable.

## 2. Let Render read the repository

When Render first asks for repository access, choose
**Only select repositories** and pick **Order-system**. To change this later:
GitHub > Settings > Applications > Render > Configure.

## 3. Create everything from the Blueprint

1. In the Render dashboard, click **New +** > **Blueprint**.
2. Choose the **Order-system** repository and the branch (`main` if you merged).
3. Render lists the three resources above. Give the Blueprint a name, e.g. `BFS Orders`.
4. Render asks for the values marked "sync: false". At the moment that is only
   **EMAIL_HOST_PASSWORD**:
   - **Testing with your own IONOS mailbox:** enter that mailbox's password and
     see section 6.
   - **Not ready for email yet:** enter any placeholder text. Orders will be
     saved and listed as failed emails until a real password is set.
5. Click **Apply**. The first build takes 5–10 minutes. Progress shows under
   each service's **Events** and **Logs** tabs.

## 4. Create the first admin account

1. Open **bfs-orders** > **Shell** (left-hand menu).
2. Type the command below and press Enter:
   ```bash
   python manage.py createsuperuser
   ```
3. Enter your email, full name and a password (at least 10 characters).

## 5. Sign in

1. The website address is shown at the top of the **bfs-orders** page, e.g.
   `https://bfs-orders.onrender.com`. Open it and sign in.
2. Import the catalogue: **Import** > choose the spreadsheet > **Upload and
   preview** > **Apply import**.
3. Staff use **Request an account** on the sign-in page. Approve them under **Users**.

## 6. Email settings

These are on **bfs-orders** > **Environment**. After changing them, click
**Save, rebuild, and deploy**.

| Variable | Testing with your own IONOS mailbox | Live |
|---|---|---|
| `EMAIL_HOST_USER` | your mailbox, e.g. `you@yourdomain.co.uk` | `materialorders@bfsuk.org` (already set) |
| `EMAIL_HOST_PASSWORD` | your mailbox password | the materialorders@ password |
| `DEFAULT_FROM_EMAIL` | **add it**, with the same address as `EMAIL_HOST_USER` | not needed (the default is correct) |
| `STORES_EMAIL` | **your own address**, so tests don't reach stores | `stores@bfsuk.org` (already set) |

To check the settings, open **Shell** and run (with your own address):
```bash
python manage.py sendtestemail you@yourdomain.co.uk
```
If it fails, the error message says why. The usual cause is a wrong password,
or a "from" address that doesn't match the mailbox.

## 7. Check the nightly job

Open **bfs-orders-retention**. Its schedule is 02:15 UTC every night. Click
**Trigger Run** to try it now. The result appears in the app under **Retention**.

## 8. Optional: your own web address

To use an address like `orders.bfsuk.org` instead of `…onrender.com`:

1. In **bfs-orders** > **Settings** > **Custom Domains**, add `orders.bfsuk.org`.
2. In IONOS, under **Domains & SSL** > **DNS**, add the CNAME record that Render shows.
3. In **bfs-orders** > **Environment**, add `ALLOWED_HOSTS` = `orders.bfsuk.org`.
   Render issues the HTTPS certificate automatically.

---

## Everyday running

- **Updates:** each time new code is merged into the deployed branch, Render
  rebuilds and redeploys automatically. Database changes are applied as part
  of each deploy.
- **Backups:** the Basic database plan includes daily backups, under
  **bfs-orders-db** > **Recovery**.
- **Secrets** (passwords, keys) live only in Render's Environment tab, never in the code.
- **If the site is down:** check **bfs-orders** > **Logs** and **Events**.
  A failed deploy leaves the previous version running.

## Troubleshooting

| Message in the deploy log | Meaning and fix |
|---|---|
| `Pre-deploy has failed` … `connection to server at "10.x.x.x", port 5432 failed: Connection refused` | The database wasn't ready yet. This is common on the very first deploy. The pre-deploy step now waits up to 5 minutes. If it still fails, check that **bfs-orders-db** shows *Available*, then on **bfs-orders** click **Manual Deploy > Deploy latest commit**. |
| `Database still not reachable after 300s` | The database is down or in a different region. Check **bfs-orders-db** is *Available* and in **Frankfurt**, the same region as the web service. |
| `SECRET_KEY environment variable is required` | Add `SECRET_KEY` under **Environment**, using **Generate**. |
| `DisallowedHost` | Add your web address to `ALLOWED_HOSTS` under **Environment**. |
| Health check failing | The check uses `/healthz`, which should return `ok`. Look in **Logs** for an error during start-up. |
