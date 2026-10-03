# Conversation: Gmail API Listener for Homelab

**Date:** 2026-10-03
**Topic:** Designing and implementing a homelab "listener" that runs a custom tool on every incoming Gmail message, using Gmail API push notifications via Google Cloud Pub/Sub.
**Deliverables produced:** `gmail-listener.zip` (full project: `auth.py`, `gmail_client.py`, `watch.py`, `renew_watch.py`, `listener.py`, `state.py`, `tool_runner.py`, systemd units, `README.md`).

---

## User

I want to set up a "listener" in my homelab that subscribes to messages from the GMail API. The goal is to run a tool I built on each email message as soon as it arrives. How would I implement this? What's the proper term for the "listener"?

## Assistant

The proper term is **Pub/Sub subscriber** (or "Gmail push notification listener"). Gmail doesn't do webhooks directly — there's no way to register your own HTTPS URL with Gmail itself. Instead it publishes change events to a **Google Cloud Pub/Sub topic**, and your homelab code is a **subscriber** to that topic.

Architecture, in order:

1. **Create a GCP project and Pub/Sub topic.** Enable the Gmail API and Cloud Pub/Sub API; create a topic, e.g. `projects/my-project/topics/gmail-notifications`.
2. **Grant Gmail permission to publish.** Add `gmail-api-push@system.gserviceaccount.com` as a Publisher on that topic (IAM on the topic itself, not project-wide). Without this, `watch()` fails.
3. **Create a subscription — pull, not push.** A pull subscription means your homelab process reaches out to Google and long-polls for messages — no public HTTPS endpoint, TLS cert, or port-forwarding needed. Push subscriptions require Google to be able to call you.
4. **Call `users.watch()` with OAuth.** Authenticate as the Gmail user (`gmail.readonly` or `gmail.modify` scope) and call `users.watch()`, passing `topicName` and optionally `labelIds` (e.g. `['INBOX']`). Response gives a `historyId` and `expiration`.
5. **Renew the watch before it expires.** A watch expires after at most 7 days. A cron job or scheduled task must call `users.watch()` again before then, or notifications silently stop.
6. **Run a long-lived subscriber process.** A Pub/Sub client library (Python `google-cloud-pubsub`, Node `@google-cloud/pubsub`) with streaming pull — this is the "listener" itself, run as a systemd service or Docker container.
7. **Fetch what actually changed.** The Pub/Sub message only contains `{emailAddress, historyId}` — never content. Call `users.history.list(startHistoryId=<last known>)` to get the delta, pull the new message IDs, then `users.messages.get()` for each. Store the latest `historyId` to resume correctly after restarts.
8. **Invoke the tool.** Hand each new message to the existing tool — run synchronously if fast, or push onto a local queue if slow, so the Pub/Sub pull loop doesn't block.

Example subscriber sketch (Python):

```python
from google.cloud import pubsub_v1
from googleapiclient.discovery import build

def callback(message):
    message.ack()  # ack immediately; Gmail will resend history if you miss one
    data = json.loads(message.data.decode("utf-8"))
    new_history_id = data["historyId"]

    history = gmail.users().history().list(
        userId="me", startHistoryId=last_seen_history_id
    ).execute()

    for record in history.get("history", []):
        for added in record.get("messagesAdded", []):
            msg_id = added["message"]["id"]
            full_msg = gmail.users().messages().get(userId="me", id=msg_id).execute()
            run_my_tool(full_msg)   # <- your existing tool

    last_seen_history_id = new_history_id

subscriber = pubsub_v1.SubscriberClient()
subscriber.subscribe(subscription_path, callback=callback)
```

Notes given:
- **Pull over push for a homelab** — avoids needing a public HTTPS endpoint/tunnel.
- **History IDs can go stale** — if the listener is down long enough, `startHistoryId` can fall outside Gmail's retention window and `history.list()` 404s; fall back to a full `messages.list()` sync in that case.
- **Persist `last_seen_history_id` to disk**, not memory, so a restart doesn't lose place or reprocess a backlog.
- **At-least-once delivery** — Pub/Sub can redeliver, so `run_my_tool()` should be idempotent (check if a message ID was already processed).

---

## User

Provide more detailed and comprehensive instructions for all 8 steps you provided, along with an example implementation

## Assistant

Built a full working project (delivered as `gmail-listener.zip`) rather than a single code block, since the implementation exceeded what belongs inline. Summary of the project:

### Files

| File | Purpose |
|---|---|
| `auth.py` | One-time interactive OAuth; caches a refresh token in `token.json` |
| `gmail_client.py` | Builds an authenticated Gmail API client |
| `watch.py` / `renew_watch.py` | Registers/renews the push notification watch |
| `listener.py` | The long-lived Pub/Sub subscriber — the "listener" itself |
| `state.py` | SQLite-backed `last_history_id` + idempotency tracking (`processed_messages` table) |
| `tool_runner.py` | Where the user's own tool gets plugged in (`run_my_tool()`) |
| `gmail-listener.service` | systemd unit to run the listener continuously (`Restart=always`) |
| `gmail-watch-renew.service` / `.timer` | systemd units to renew the watch daily |
| `requirements.txt` | `google-api-python-client`, `google-auth`, `google-auth-oauthlib`, `google-cloud-pubsub`, `python-dotenv` |
| `.env.example` | Config template: project ID, topic/subscription names, label filter, file paths |
| `README.md` | Full step-by-step walkthrough, mapped to all 8 steps, with `gcloud` commands |

### Key design decisions

- **Pull subscription, not push** — confirmed again as the right call for a homelab with no public endpoint.
- **Idempotency handled centrally** — `listener.py` checks `state.already_processed(msg_id)` before calling the tool and records it after, rather than leaving this to the tool itself.
- **Ack-after-success, not ack-immediately** — `listener.py` only acks the Pub/Sub message after the history fetch and dispatch succeed; on exception it nacks so Pub/Sub redelivers instead of silently dropping work.
- **Explicit handling of the stale-`historyId`-404 case** — catches `HttpError` status 404 from `history.list()`, logs a warning, and resets the baseline to the notification's `historyId` (meaning the gap isn't replayed unless a full reconciliation pass is added).
- **A broken tool doesn't crash the listener** — `run_my_tool()` is wrapped in try/except inside the dispatch loop, logged via `log.exception`, and the message is still marked processed so a permanently-broken message doesn't loop forever.

### Step-by-step README contents (condensed)

1. **GCP project + topic:**
   ```bash
   gcloud projects create my-gmail-listener-project
   gcloud config set project my-gmail-listener-project
   gcloud services enable gmail.googleapis.com pubsub.googleapis.com
   gcloud pubsub topics create gmail-notifications
   ```
2. **Grant Gmail publish permission:**
   ```bash
   gcloud pubsub topics add-iam-policy-binding gmail-notifications \
     --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" \
     --role="roles/pubsub.publisher"
   ```
3. **Pull subscription:**
   ```bash
   gcloud pubsub subscriptions create gmail-notifications-sub \
     --topic=gmail-notifications \
     --ack-deadline=60
   ```
4. **OAuth + `users.watch()`:** Create a Desktop-app OAuth client in Cloud Console, save as `credentials.json`, copy `.env.example` → `.env`, run `python auth.py` (interactive, once) then `python watch.py`.
5. **Renew before expiration (≤7 days):** systemd timer (`gmail-watch-renew.timer`, daily) or a cron line calling `renew_watch.py`.
6. **Run the listener:** `pip install -r requirements.txt && python listener.py`, or deployed under `gmail-listener.service` with `Restart=always`.
7. **Resolve notifications → messages:** `history.list(startHistoryId=..., historyTypes=["messageAdded"])` → `messages.get()` per new ID → store new `historyId` in `state.sqlite3`.
8. **Invoke the tool:** edit `tool_runner.py`'s `run_my_tool()`; keep the idempotency check and avoid blocking the callback for slow work (use a local queue instead).

### Troubleshooting notes included in the README

- No notifications ever arrive → check the IAM binding is on the *topic* and the topic name matches the project ID exactly.
- Notifications stop after ~7 days → renewal timer/cron isn't running.
- Constant `history.list` 404s → listener was down past Gmail's retention window; self-heals by design.
- Duplicate tool invocations → `state.sqlite3` isn't on persistent storage (e.g. was placed in `/tmp` in a container that resets).

---

## User

When I run `renew_watch.py` once per week, does that process require human interaction? If so, is there any way to automate it?

## Assistant

**Normal case: no human interaction required.** `renew_watch.py` → `watch.py` → `auth.get_credentials()` loads `token.json`, sees the access token expired, and calls `creds.refresh(Request())` — a plain HTTPS POST to Google's token endpoint using the refresh token. No browser involved. The interactive `run_local_server()` flow only triggers if there's no `token.json` or no refresh token in it.

**The likely actual cause of weekly interaction:** if the OAuth consent screen is still in **"Testing"** publishing status (the default from the setup walkthrough — "add yourself as a test user"), Google treats the app as unverified and forces the refresh token to expire after **exactly 7 days**, regardless of activity. When that happens, `creds.refresh()` fails with `invalid_grant`, and `auth.py` falls back to the interactive flow — which can't run headlessly. This matches a weekly cadence closely.

### Fix — publish the OAuth app

1. **Open the OAuth consent screen** in Cloud Console (APIs & Services → OAuth consent screen) for the same project.
2. **Click "Publish App"** — moves Publishing status from Testing to In production. For a personal, single-user tool this does *not* require Google's verification review; verification is a separate, optional step mainly for apps used by many external users or requesting sensitive scopes at scale.
3. **Click through once more if prompted** — because `gmail.modify` is a restricted scope and the app isn't verified, the next interactive consent may show an "unverified app" warning. Advanced → "Go to (app name) (unsafe)" is just Google's warning screen, not a hard block, for the app's own owner.
4. **Delete `token.json` and re-run `python auth.py` once more** — refresh tokens issued while still in Testing stay on the 7-day clock even after publishing; a fresh token must be issued post-publish.
5. **Confirm it's fixed** — refresh tokens issued to a Published app don't expire on a timer; they only die if revoked or unused for about 6 months. `renew_watch.py` should then run indefinitely via cron/systemd with zero interaction.

### Other edge case noted

A refresh token is also invalidated if unused for ~6 months, or if access is revoked from the [Google Account permissions page](https://myaccount.google.com/permissions). Since the listener calls `get_credentials()` constantly during normal operation, this is only a practical risk if the listener itself is down for a long stretch.

Offered (not yet done, pending user decision): updating the README with this publishing step, and adding loud-warning logging in `renew_watch.py` if a refresh attempt fails, instead of silently trying to launch a browser on a headless box.

---

## User

I want to preserve this conversation in a Markdown file so I can reference it in a conversation about multiple chat sessions. ... save the output to this directory: `W:\dev\sandbox\jev-gmail-labeler\prompts`

## Assistant

Generated this file. Noted that the assistant's sandboxed environment has no access to the user's local Windows filesystem, so the file was produced for download rather than written directly to `W:\dev\sandbox\jev-gmail-labeler\prompts` — the user needs to save it into that folder themselves.
