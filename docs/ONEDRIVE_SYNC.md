# Nightly void tracker sync from OneDrive

The voids app can read the Excel void tracker straight from the Building Finishing
Services OneDrive every night, using Microsoft Graph. It only **reads** the file; it never
changes it. Until this is set up, the sync can still be run by uploading the file on
**Admin → Tracker sync**.

## 1. Register the app in Microsoft 365 (10 minutes, needs a Microsoft 365 admin)

1. Go to <https://entra.microsoft.com> → **Identity → Applications → App registrations → New registration**.
2. Name: `BFS Voids tracker sync`. Supported account types: **this organisation only**. No redirect URI. **Register**.
3. On the app's **Overview** page, copy:
   - **Directory (tenant) ID** → `MS_TENANT_ID`
   - **Application (client) ID** → `MS_CLIENT_ID`
4. **Certificates & secrets → New client secret** (24 months). Copy the **Value** straight away → `MS_CLIENT_SECRET`.
   Put a reminder in the diary to renew it before it expires.
5. **API permissions → Add a permission → Microsoft Graph → Application permissions →
   `Files.Read.All`** → Add. Then press **Grant admin consent for Building Finishing Services**.

   (`Files.Read.All` lets the app read files in the organisation's OneDrive/SharePoint. If IT
   prefer to limit it to one SharePoint site, use `Sites.Selected` instead and grant the app
   read access to the site holding the tracker.)

## 2. Point it at the tracker

In OneDrive, on the tracker: **Share → Copy link** (a link for people in Building Finishing
Services is fine). That address → `VOIDS_TRACKER_URL`.

(Alternatively set `VOIDS_TRACKER_PATH` to `owner@bfsuk.org:/Folder/Void Tracker.xlsx`.)

## 3. Add the settings in Render

Add `MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET` and `VOIDS_TRACKER_URL` to **both**:

- the web service **bfs-orders** (for the *Sync from OneDrive now* button), and
- the nightly job **bfs-orders-retention** (its start command runs
  `python manage.py sync_void_tracker` before the supervisor email).

Then open **Admin → Tracker sync** and press **Sync from OneDrive now** to test it.

## How the sync decides (safe rules)

For every void, field and trade it compares what the tracker said last time, what it says
now, and what the app says:

| Situation | Result |
|---|---|
| Only the tracker changed | The app takes the tracker's value |
| Only the app changed | The app's value is kept |
| Both changed, differently | **Conflict**: app value kept, listed on Tracker sync to decide |
| Void ready for final check or completed | Never changed by the tracker; differences are conflicts |
| New row in the tracker | New void (needs visit) |
| Row gone from the tracker | Nothing deleted; listed as "no longer in the tracker" |

The first sync after switching this on has no "last time" to compare with, so any
difference is listed as a conflict. Use **Keep the app's values** for all of them if the app
is up to date. Admins are emailed after any sync that changed something or found conflicts.
