"""
Registers (or re-registers) the Gmail push-notification watch.

A watch expires after at most 7 days (Google's hard limit), so this must be
re-run before then or notifications silently stop. renew_watch.py is a thin
CLI entrypoint around start_watch() meant to be run on a daily timer.
"""

import logging
import os

from dotenv import load_dotenv

import state
from gmail_client import GMAIL_USER, get_gmail_service

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("watch")

PUBSUB_TOPIC = os.environ["PUBSUB_TOPIC"]
LABEL_IDS = [l.strip() for l in os.environ.get("WATCH_LABEL_IDS", "").split(",") if l.strip()]


def start_watch() -> dict:
    service = get_gmail_service()

    body = {"topicName": PUBSUB_TOPIC}
    if LABEL_IDS:
        body["labelIds"] = LABEL_IDS

    response = service.users().watch(userId=GMAIL_USER, body=body).execute()

    # response looks like: {"historyId": "1234567", "expiration": "1700000000000"}
    log.info(
        "Watch registered. historyId=%s expiration=%s",
        response["historyId"],
        response["expiration"],
    )

    # Only seed last_history_id if we don't already have one -- on a renewal,
    # keep whatever history we've already processed up to, so we don't skip
    # mail that arrived in the gap between the old watch's last notification
    # and this renewal call.
    if state.get_last_history_id() is None:
        state.set_last_history_id(response["historyId"])

    return response


if __name__ == "__main__":
    start_watch()
