"""
Replace the body of run_my_tool() with a call into your actual tool.

`message` is the full resource returned by users.messages.get() -- see
https://developers.google.com/gmail/api/reference/rest/v1/users.messages#Message
By default it's fetched with format="full", so message["payload"] has
headers, mimeType, and the body (base64url-encoded for the leaf parts).

Keep this function reasonably fast. If your tool can take more than a
second or two, push the message onto a local queue (e.g. a `queue.Queue`
consumed by a worker thread/process, or Redis/RQ if you want something
that survives a crash) instead of running it inline here -- otherwise
you'll block the Pub/Sub streaming-pull callback and risk the broker
redelivering the same message because you didn't ack in time.
"""

import logging

log = logging.getLogger("tool_runner")


def run_my_tool(message: dict) -> None:
    headers = {h["name"]: h["value"] for h in message["payload"].get("headers", [])}
    subject = headers.get("Subject", "(no subject)")
    sender = headers.get("From", "(unknown sender)")

    log.info("New message %s from %s: %r", message["id"], sender, subject)

    # --- replace below with your actual tool ---
    # e.g. my_tool.process(message)
    # or:  subprocess.run(["/path/to/my-tool", "--message-id", message["id"]], check=True)
