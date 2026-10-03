"""
The "listener": a long-lived Pub/Sub subscriber.

Run it with:
    python listener.py

It blocks forever, invoking `_on_notification` each time Gmail publishes a
change. In production, run it under systemd (see gmail-listener.service)
so it restarts automatically on crash or reboot.

Flow per notification:
  1. Pub/Sub delivers {"emailAddress": ..., "historyId": ...}. This does
     NOT contain message content.
  2. We call users.history.list(startHistoryId=<our last known id>) to get
     everything that changed since we last looked.
  3. For each messagesAdded entry, fetch the full message and hand it to
     tool_runner.run_my_tool(), unless we've already processed that ID.
  4. We advance our stored last_history_id to the notification's historyId.
"""

import json
import logging
import os
import signal
import sys

from dotenv import load_dotenv
from google.cloud import pubsub_v1
from googleapiclient.errors import HttpError

import state
from gmail_client import GMAIL_USER, get_gmail_service
from tool_runner import run_my_tool

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("listener")

SUBSCRIPTION_PATH = os.environ["PUBSUB_SUBSCRIPTION"]


def _fetch_and_dispatch_new_messages(gmail, start_history_id: str, new_history_id: str) -> None:
    """Pull the history delta and hand any newly added messages to the tool."""
    page_token = None

    while True:
        try:
            resp = (
                gmail.users()
                .history()
                .list(
                    userId=GMAIL_USER,
                    startHistoryId=start_history_id,
                    historyTypes=["messageAdded"],
                    pageToken=page_token,
                )
                .execute()
            )
        except HttpError as e:
            if e.resp.status == 404:
                # start_history_id is too old (past Gmail's retention window),
                # typically because the listener was down for a while.
                # Fall back to just recording the new baseline; messages that
                # arrived during the gap won't be replayed. If you need a
                # true no-miss guarantee, do a full users.messages.list()
                # reconciliation here instead of just moving the pointer.
                log.warning(
                    "startHistoryId %s is stale (404). Resetting baseline to %s "
                    "without replaying the gap.",
                    start_history_id,
                    new_history_id,
                )
                state.set_last_history_id(new_history_id)
                return
            raise

        for record in resp.get("history", []):
            for added in record.get("messagesAdded", []):
                msg_id = added["message"]["id"]

                if state.already_processed(msg_id):
                    continue

                full_msg = (
                    gmail.users()
                    .messages()
                    .get(userId=GMAIL_USER, id=msg_id, format="full")
                    .execute()
                )

                try:
                    run_my_tool(full_msg)
                except Exception:
                    # A broken tool shouldn't crash the listener or cause
                    # redelivery loops; log it and move on. Consider adding
                    # a dead-letter mechanism if silent failures are unacceptable.
                    log.exception("run_my_tool failed for message %s", msg_id)

                state.mark_processed(msg_id)

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    state.set_last_history_id(new_history_id)


def _on_notification(message: pubsub_v1.subscriber.message.Message) -> None:
    try:
        payload = json.loads(message.data.decode("utf-8"))
        new_history_id = str(payload["historyId"])
        log.info("Notification received: %s", payload)

        gmail = get_gmail_service()
        start_history_id = state.get_last_history_id() or new_history_id

        _fetch_and_dispatch_new_messages(gmail, start_history_id, new_history_id)

        # Ack only after successful processing -- if something above raised,
        # we fall through to nack() and Pub/Sub will redeliver.
        message.ack()
    except Exception:
        log.exception("Failed to process notification; nacking for redelivery")
        message.nack()


def main() -> None:
    subscriber = pubsub_v1.SubscriberClient()
    future = subscriber.subscribe(SUBSCRIPTION_PATH, callback=_on_notification)
    log.info("Listening on %s ... (Ctrl+C to stop)", SUBSCRIPTION_PATH)

    def _shutdown(signum, frame):
        log.info("Shutting down...")
        future.cancel()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        future.result()
    except Exception:
        log.exception("Subscriber stopped unexpectedly")
        sys.exit(1)


if __name__ == "__main__":
    main()
