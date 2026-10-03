"""
OAuth helper for the Gmail listener.

Run this file directly ONCE, interactively, on a machine with a browser:

    python auth.py

It opens a consent screen, you approve access, and it writes token.json
next to this script. After that, listener.py and renew_watch.py load
token.json and refresh the access token automatically -- no browser or
human interaction needed, so this runs fine headless in the homelab.

If you truly cannot open a browser on the target machine (e.g. a
headless server with no port you can forward), run this script on your
laptop instead, then copy the resulting token.json over to the server.
"""

import os

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

# gmail.modify lets your tool mark messages read/archive/label them;
# use gmail.readonly if your tool only ever reads messages.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

CLIENT_SECRET_FILE = os.environ.get("OAUTH_CLIENT_SECRET_FILE", "credentials.json")
TOKEN_FILE = os.environ.get("OAUTH_TOKEN_FILE", "token.json")


def get_credentials() -> Credentials:
    """Load cached credentials, refreshing or running the consent flow as needed."""
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save(creds)
        return creds

    # No valid cached token -- run the interactive flow.
    if not os.path.exists(CLIENT_SECRET_FILE):
        raise FileNotFoundError(
            f"{CLIENT_SECRET_FILE} not found. Download an OAuth 'Desktop app' "
            "client secret from Google Cloud Console > APIs & Services > "
            "Credentials, and save it under that name."
        )

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    creds = flow.run_local_server(port=0)
    _save(creds)
    return creds


def _save(creds: Credentials) -> None:
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())


if __name__ == "__main__":
    get_credentials()
    print(f"Authorized. Token cached at {TOKEN_FILE}. You can now run listener.py headlessly.")
