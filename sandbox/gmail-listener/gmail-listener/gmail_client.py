import os

from googleapiclient.discovery import build

from auth import get_credentials

GMAIL_USER = os.environ.get("GMAIL_USER", "me")


def get_gmail_service():
    creds = get_credentials()
    # cache_discovery=False avoids a noisy warning in containerized environments
    return build("gmail", "v1", credentials=creds, cache_discovery=False)
