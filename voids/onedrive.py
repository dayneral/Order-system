"""
Download the void tracker from the company OneDrive / SharePoint with Microsoft Graph.

Set these environment variables (see docs/ONEDRIVE_SYNC.md):

    MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET   the app registration
    VOIDS_TRACKER_URL     a sharing link to the workbook (the "Copy link" address), or
    VOIDS_TRACKER_PATH    "user@bfsuk.org:/Folder/Void Tracker.xlsx" (the owner's OneDrive and path)
"""

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.microsoft.com/v1.0"


class OneDriveError(Exception):
    pass


def configured():
    return all(os.environ.get(k) for k in ("MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET")) and bool(
        os.environ.get("VOIDS_TRACKER_URL") or os.environ.get("VOIDS_TRACKER_PATH"))


def _token():
    body = urllib.parse.urlencode({
        "client_id": os.environ["MS_CLIENT_ID"], "client_secret": os.environ["MS_CLIENT_SECRET"],
        "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"}).encode()
    url = f"https://login.microsoftonline.com/{os.environ['MS_TENANT_ID']}/oauth2/v2.0/token"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=30) as resp:
            return json.load(resp)["access_token"]
    except urllib.error.HTTPError as exc:
        raise OneDriveError(f"Microsoft sign-in failed ({exc.code}): check MS_TENANT_ID, MS_CLIENT_ID and "
                            f"MS_CLIENT_SECRET. {exc.read()[:300]!r}") from exc


def content_url():
    link = os.environ.get("VOIDS_TRACKER_URL")
    if link:
        share_id = "u!" + base64.urlsafe_b64encode(link.encode()).decode().rstrip("=")
        return f"{GRAPH}/shares/{share_id}/driveItem/content"
    owner, _, path = os.environ["VOIDS_TRACKER_PATH"].partition(":")
    return f"{GRAPH}/users/{urllib.parse.quote(owner)}/drive/root:{urllib.parse.quote(path)}:/content"


def download():
    """Return (filename, bytes) of the workbook."""
    if not configured():
        raise OneDriveError("The OneDrive sync is not set up yet (see docs/ONEDRIVE_SYNC.md).")
    req = urllib.request.Request(content_url(), headers={"Authorization": f"Bearer {_token()}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return "Void Tracker (OneDrive).xlsx", resp.read()
    except urllib.error.HTTPError as exc:
        hint = {401: "the app is not allowed to read files (check the Files.Read.All permission and admin consent)",
                403: "the app is not allowed to read this file",
                404: "the file was not found (check VOIDS_TRACKER_URL / VOIDS_TRACKER_PATH)"}.get(exc.code, "")
        raise OneDriveError(f"Could not download the tracker ({exc.code}){': ' + hint if hint else ''}.") from exc
