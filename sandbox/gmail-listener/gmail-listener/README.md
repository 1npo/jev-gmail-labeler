# Gmail Pub/Sub Listener

A homelab-friendly "listener" that runs your tool on every incoming Gmail
message, built on Gmail's push-notification mechanism (Cloud Pub/Sub), using
a **pull subscription** so nothing needs to be publicly reachable.

Commands below use the `gcloud` CLI. Install it first if you don't have it:
https://cloud.google.com/sdk/docs/install, then run `gcloud init` and
`gcloud auth login`.

---

## Step 1 — Create a GCP project and Pub/Sub topic

```bash
# Create a dedicated project (skip if reusing an existing one)
gcloud projects create my-gmail-listener-project
gcloud config set project my-gmail-listener-project

# A project needs billing enabled to use Pub/Sub, even within the free tier.
# Do this once in the console: https://console.cloud.google.com/billing

# Enable the two APIs you need
gcloud services enable gmail.googleapis.com pubsub.googleapis.com

# Create the topic Gmail will publish change events to
gcloud pubsub topics create gmail-notifications
```

Note the full topic name you'll need later:
`projects/my-gmail-listener-project/topics/gmail-notifications`

---

## Step 2 — Grant Gmail permission to publish to the topic

Gmail publishes as a fixed Google-owned service account. It needs the
Pub/Sub Publisher role **on that specific topic** (not project-wide):

```bash
gcloud pubsub topics add-iam-policy-binding gmail-notifications \
  --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" \
  --role="roles/pubsub.publisher"
```

If you skip this, `users.watch()` in Step 4 will fail with a permission
error referencing that service account.

---

## Step 3 — Create a subscription (pull, not push)

```bash
gcloud pubsub subscriptions create gmail-notifications-sub \
  --topic=gmail-notifications \
  --ack-deadline=60
```

`--ack-deadline=60` gives your listener 60 seconds to process a message
before Pub/Sub assumes it failed and redelivers it. Raise it if
`run_my_tool()` is ever slow — or better, keep it short and offload slow
work to a queue (see the note in `tool_runner.py`).

We deliberately use **pull**, not push: a push subscription requires Google
to call an HTTPS endpoint you control, which means a public domain, a valid
TLS cert, and usually port-forwarding or a tunnel (Cloudflare Tunnel,
Tailscale Funnel, ngrok). Pull means your homelab process calls out to
Google instead, so none of that is needed.

---

## Step 4 — Get OAuth credentials and call `users.watch()`

1. In the [Cloud Console](https://console.cloud.google.com/apis/credentials),
   go to **APIs & Services → Credentials → Create Credentials → OAuth client
   ID**.
2. If prompted, configure the OAuth consent screen first (External is fine
   for personal use; add yourself as a test user if the app stays in
   "Testing" status).
3. Application type: **Desktop app**. Download the JSON and save it as
   `credentials.json` in this project directory.
4. Copy `.env.example` to `.env` and fill in your project ID, topic, and
   subscription names.
5. Run the one-time interactive auth:
   ```bash
   python auth.py
   ```
   This opens a browser for consent and writes `token.json`, which contains
   a refresh token — after this, nothing in the pipeline needs a browser
   again, including on headless machines (do this step on a machine with a
   browser, then copy `token.json` over if your homelab box has none).
6. Register the watch:
   ```bash
   python watch.py
   ```
   This calls `users.watch()` with your topic name and prints back a
   `historyId` and `expiration`. That `historyId` becomes your starting
   point for reading changes.

---

## Step 5 — Renew the watch before it expires

Gmail watches expire after **at most 7 days** — there's no way to make one
permanent. Set up `renew_watch.py` to run daily, well inside that window.

**Option A — systemd timer (recommended):**
```bash
sudo cp gmail-watch-renew.service gmail-watch-renew.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gmail-watch-renew.timer
systemctl list-timers gmail-watch-renew.timer   # confirm it's scheduled
```

**Option B — plain cron**, if you'd rather not touch systemd:
```bash
crontab -e
# add:
0 3 * * * cd /opt/gmail-listener && /opt/gmail-listener/.venv/bin/python renew_watch.py >> /var/log/gmail-watch-renew.log 2>&1
```

Edit the `WorkingDirectory` / paths in the `.service` files to match
wherever you actually deploy this (the examples assume `/opt/gmail-listener`
with a venv at `.venv`).

---

## Step 6 — Run the long-lived subscriber process

This is the actual "listener" — a process that stays connected to Pub/Sub
and fires a callback per notification.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python listener.py
```

For a persistent homelab deployment, run it under systemd so it survives
crashes and reboots:

```bash
sudo mkdir -p /opt/gmail-listener
sudo cp -r . /opt/gmail-listener
cd /opt/gmail-listener
python -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo cp gmail-listener.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gmail-listener
journalctl -u gmail-listener -f   # watch the logs
```

---

## Step 7 — Resolve notifications into actual messages

A Pub/Sub notification is just `{"emailAddress": ..., "historyId": ...}` —
no subject, sender, or body. `listener.py` handles the rest automatically:

1. Calls `users.history.list(startHistoryId=<last stored id>)` to get
   everything that changed since the last notification.
2. Filters for `messagesAdded` entries (ignoring reads, label changes,
   deletions, etc., since `historyTypes=["messageAdded"]` is passed).
3. Calls `users.messages.get()` for each new message ID to fetch the
   full content.
4. Stores the new `historyId` in `state.sqlite3` so a restart resumes from
   the right place instead of replaying everything or losing the gap.

If the listener has been offline long enough that `startHistoryId` falls
outside Gmail's retention window, `history.list()` returns a 404. The code
catches this, logs a warning, and resets its baseline to the notification's
`historyId` — meaning messages from the gap are **not** replayed. If you
need a stronger guarantee, replace that fallback with a full
`users.messages.list()` reconciliation pass.

---

## Step 8 — Invoke your tool

Edit `tool_runner.py` — specifically the body of `run_my_tool()` — to call
into whatever you actually built. The function receives the full Gmail
message resource (`format="full"`), so headers, MIME structure, and
base64url-encoded bodies are all there.

Two things worth keeping even if you rewrite this file:

- **Idempotency.** Pub/Sub is at-least-once, so the same message can be
  delivered twice. `listener.py` already checks `state.already_processed()`
  before calling your tool and records it after — keep that, or replicate
  the same check inside your tool if you move the dispatch logic elsewhere.
- **Don't block the callback for long-running work.** If your tool can take
  more than a second or two, hand the message off to a local queue (a
  `queue.Queue` + worker thread, or Redis/RQ for something crash-safe)
  instead of running it inline, so you ack back to Pub/Sub quickly and
  don't risk redelivery from a timed-out ack deadline.

---

## File overview

| File | Purpose |
|---|---|
| `auth.py` | One-time interactive OAuth; caches a refresh token |
| `gmail_client.py` | Builds an authenticated Gmail API client |
| `watch.py` / `renew_watch.py` | Registers/renews the push notification watch |
| `listener.py` | The long-lived Pub/Sub subscriber — the "listener" itself |
| `state.py` | SQLite-backed historyId + idempotency tracking |
| `tool_runner.py` | Where you plug in your own tool |
| `gmail-listener.service` | systemd unit to run the listener continuously |
| `gmail-watch-renew.service/.timer` | systemd units to renew the watch daily |

## Troubleshooting

- **No notifications ever arrive**: confirm Step 2's IAM binding is on the
  *topic*, not the project; confirm the topic name passed to `watch()`
  exactly matches your project ID (Gmail validates this strictly).
- **Notifications stop after ~7 days**: the renewal timer/cron isn't
  running — check `systemctl status gmail-watch-renew.timer` or your
  crontab.
- **`history.list` 404s constantly**: the listener was down for longer than
  Gmail's history retention; this is expected and self-heals (see Step 7).
- **Duplicate tool invocations**: confirm `state.sqlite3` is on persistent
  storage and not being wiped between restarts (e.g. don't put it in `/tmp`
  in a container that resets).
