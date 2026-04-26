"""One-time local OAuth bootstrap.

Run on the user's Mac, exactly once. Opens a browser, has the user grant
Drive access, then prints a JSON blob with the refresh token and OAuth client
identifiers. That blob is what we store on Render as ``GOOGLE_CREDENTIALS_JSON``.

Usage:
  1. Drop your Desktop OAuth client secret next to this file as
     ``client_secret.json`` (download from Google Cloud Console).
  2. ``python3 bootstrap_oauth.py``
  3. Browser opens → sign in → Allow → terminal prints ``credentials.json``.
  4. Open ``credentials.json`` and copy its contents into the
     ``GOOGLE_CREDENTIALS_JSON`` env var on Render.

The resulting refresh token does not expire as long as the user doesn't revoke
it in https://myaccount.google.com/permissions and the OAuth client stays in
the project. If it ever expires, just rerun this script.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]
HERE = Path(__file__).parent
SECRET = HERE / "client_secret.json"
OUT = HERE / "credentials.json"


def main() -> None:
    if not SECRET.exists():
        print(f"Missing {SECRET.name}. Download a Desktop OAuth client from\n"
              "Google Cloud Console → APIs & Services → Credentials → "
              "Create Credentials → OAuth client ID → Desktop app.\n"
              f"Save the downloaded JSON as {SECRET}.")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(SECRET), scopes=SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    if not creds.refresh_token:
        print("No refresh token received. This usually means you previously consented;\n"
              "go to https://myaccount.google.com/permissions, revoke the app, then rerun.")
        sys.exit(2)

    payload = {
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
    }
    OUT.write_text(json.dumps(payload, indent=2))
    print(f"\n✅ Saved {OUT}\n")
    print("Copy this single-line value into Render's GOOGLE_CREDENTIALS_JSON env var:\n")
    print(json.dumps(payload))


if __name__ == "__main__":
    main()
