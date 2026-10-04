# User guide

`jev-gmail-labeler` sorts your Gmail inbox into labels. It reads each new email, asks [Jev](ABOUT_JEV.md) (a fast text classifier from TypeSafe) which category fits best, and applies the matching Gmail label.

## Contents

1. [What it does](#1-what-it-does)
2. [What you need](#2-what-you-need)
3. [Install](#3-install)
4. [Set up Google Cloud](#4-set-up-google-cloud)
5. [Configure](#5-configure)
6. [Authorize Gmail](#6-authorize-gmail)
7. [Write your email criteria](#7-write-your-email-criteria)
8. [Run it manually](#8-run-it-manually)
9. [Run it as a service](#9-run-it-as-a-service)
10. [Maintenance](#10-maintenance)
11. [Troubleshooting](#11-troubleshooting)
12. [Uninstall](#12-uninstall)

## 1. What it does

For each email, the tool runs four steps:

1. **Fetch** the message from Gmail and turn it into clean plain text.
2. **Anonymize** the body. Names, phone numbers, addresses and similar details are replaced by placeholders such as `<PERSON>`.
3. **Classify** it. The anonymized email goes to Jev, which picks one of your categories.
4. **Label** it. The tool applies the Gmail label you chose for that category.

**What leaves your machine.** TypeSafe (the company behind Jev) receives the headers (From, To, Reply-To, Date, Subject) and the anonymized body. Google sees only the label changes and the usual Gmail API calls. The service never sends email text to Google.

Two ways to run it:

- **Manually** with `label`, for a batch of existing emails.
- **As a service** with `listen`. Gmail tells Google Pub/Sub (a message queue) about each new email, and the service pulls those notifications and labels the mail within seconds.

## 2. What you need

- Linux with systemd (only for running the service; manual use works anywhere Python does)
- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- A Google account (Gmail)
- A TypeSafe API key
- Optional: the [gcloud CLI](https://cloud.google.com/sdk/docs/install), for the command-line version of the Google Cloud steps

## 3. Install

```
uv tool install git+https://github.com/1npo/jev-gmail-labeler.git
```

This puts `jev-gmail-labeler` in `~/.local/bin`. About 33 MB of the download is the spaCy language model (spaCy is the language library that finds names in text).

Check it works:

```
jev-gmail-labeler --version
```

### Use the larger language model

The default model is `en_core_web_md`. The larger `en_core_web_lg` finds names more reliably, but takes about 400 MB of disk and starts more slowly. To use it:

```
uv tool install --with "en-core-web-lg @ https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl" git+https://github.com/1npo/jev-gmail-labeler.git
```

Then set `"spacy_model": "en_core_web_lg"` in your config file (see [Configure](#5-configure)).

## 4. Set up Google Cloud

You do this once. The tool needs a Google Cloud project with the Gmail and Pub/Sub APIs, an OAuth client (so it can act as you), and a Pub/Sub topic and subscription (for notifications). If you only plan to use `label` manually, you only need steps 4.1 to 4.3 and 4.7.

Pub/Sub may ask for a billing account. The volume of a personal mailbox fits well inside the free tier.

The `gcloud` commands below assume you have run `gcloud auth login` and `gcloud config set project YOUR_PROJECT_ID`.

### 4.1 Create a project and enable the APIs

**Console:** open the [Cloud Console](https://console.cloud.google.com/), create a project, then go to **APIs & Services → Library** and enable **Gmail API** and **Cloud Pub/Sub API**.

**gcloud:**

```
gcloud projects create YOUR_PROJECT_ID
gcloud config set project YOUR_PROJECT_ID
gcloud services enable gmail.googleapis.com pubsub.googleapis.com
```

### 4.2 Configure the consent screen

**Console:** go to **Google Auth Platform** and complete:

1. **Branding:** an app name (for example `jev-gmail-labeler`) and your email.
2. **Audience:** choose **External** and add your own Google address as a test user.
3. **Data Access:** click **Add or remove scopes** and add both:
   - `https://www.googleapis.com/auth/gmail.modify`
   - `https://www.googleapis.com/auth/pubsub`

There is no `gcloud` command for this step.

### 4.3 Create the OAuth client

**Console:** go to **Google Auth Platform → Clients → Create client**, choose **Desktop app**, and download the JSON file. Save it as:

```
~/.config/jev-gmail-labeler/credentials.json
```

```
mkdir -p ~/.config/jev-gmail-labeler
mv ~/Downloads/client_secret_*.json ~/.config/jev-gmail-labeler/credentials.json
chmod 600 ~/.config/jev-gmail-labeler/credentials.json
```

There is no `gcloud` command for creating a Desktop client.

### 4.4 Create the topic

The topic is the channel Gmail posts "new mail" notifications to.

**Console:** **Pub/Sub → Topics → Create topic**, ID `gmail-labeler`. Untick "Add a default subscription".

**gcloud:**

```
gcloud pubsub topics create gmail-labeler
```

The full topic name is `projects/YOUR_PROJECT_ID/topics/gmail-labeler`. You will need it in [Configure](#5-configure).

### 4.5 Let Gmail publish to the topic

Gmail posts notifications using a Google-owned service account, `gmail-api-push@system.gserviceaccount.com`. It must have the **Pub/Sub Publisher** role on your topic. Without this, you will get no notifications and no error.

**Console:** open the topic, go to the **Permissions** tab (side panel), click **Add principal**, enter `gmail-api-push@system.gserviceaccount.com`, and choose the role **Pub/Sub Publisher**.

**gcloud:**

```
gcloud pubsub topics add-iam-policy-binding gmail-labeler \
  --member=serviceAccount:gmail-api-push@system.gserviceaccount.com \
  --role=roles/pubsub.publisher
```

### 4.6 Create the subscription

The subscription is where the service pulls notifications from. Two settings matter:

- `--ack-deadline=300` gives the service five minutes to handle a notification before Pub/Sub sends it again.
- `--expiration-period=never` stops Google deleting the subscription. By default, a subscription with no activity for 31 days is deleted, which would silently stop the service.

**Console:** **Pub/Sub → Subscriptions → Create subscription**. ID `gmail-labeler-sub`, choose the topic, delivery type **Pull**, acknowledgement deadline **300 seconds**, and under **Expiration period** choose **Never expire**.

**gcloud:**

```
gcloud pubsub subscriptions create gmail-labeler-sub \
  --topic=gmail-labeler \
  --ack-deadline=300 \
  --expiration-period=never
```

The full name is `projects/YOUR_PROJECT_ID/subscriptions/gmail-labeler-sub`.

### 4.7 Publish the app

While the app is in **Testing** status, Google expires your login token after 7 days, and you would have to re-authorize every week. Publishing the app removes that limit. You do not need Google's verification, because you are the only user.

**Console:** **Google Auth Platform → Audience → Publish app**, then confirm.

The first time you authorize (see [Authorize Gmail](#6-authorize-gmail)), Google shows **"Google hasn't verified this app"**. That is expected, since you wrote the app. Click **Advanced**, then **Go to *app name* (unsafe)**, and continue.

If you authorized before publishing, or before adding the `pubsub` scope, delete the old token and authorize again.

## 5. Configure

Settings come from several places. Later ones win:

1. Built-in defaults
2. Environment variables named `JEV_LABELER_<KEY>` (for example `JEV_LABELER_JEV_MODEL`)
3. The config file: `--config PATH`, or else `~/.config/jev-gmail-labeler/config.json` if it exists
4. `--config-json '{"jev_model": "jev-1.13.0"}'`
5. Individual command-line flags

Create `~/.config/jev-gmail-labeler/config.json`. A minimal one for the service:

```json
{
  "pubsub_topic": "projects/YOUR_PROJECT_ID/topics/gmail-labeler",
  "pubsub_subscription": "projects/YOUR_PROJECT_ID/subscriptions/gmail-labeler-sub"
}
```

A fuller example:

```json
{
  "pubsub_topic": "projects/YOUR_PROJECT_ID/topics/gmail-labeler",
  "pubsub_subscription": "projects/YOUR_PROJECT_ID/subscriptions/gmail-labeler-sub",
  "criteria_file": "criteria.json",
  "jev_model": "jev-latest",
  "max_body_chars": 8000,
  "spacy_model": "en_core_web_md",
  "log_level": "INFO"
}
```

Relative paths in the config file are relative to the config file's folder. Relative paths from environment variables, `--config-json` and flags are relative to the current directory. A leading `~` is expanded.

### The TypeSafe API key

The key is **not** a config setting, and is rejected if you put it in the config file. Provide it in one of two ways:

- Set `typesafe_api_key_file` to a file that contains only the key, or
- Set the `TYPESAFE_API_KEY` environment variable.

For the systemd service, put it in an `env` file that only you can read:

```
echo 'TYPESAFE_API_KEY=your-key-here' > ~/.config/jev-gmail-labeler/env
chmod 600 ~/.config/jev-gmail-labeler/env
```

### Config reference

| Key | Default | Meaning |
|---|---|---|
| `credentials_file` | `~/.config/jev-gmail-labeler/credentials.json` | OAuth client file from step 4.3 |
| `token_file` | `~/.config/jev-gmail-labeler/token.json` | Where `auth` saves your login token |
| `criteria_file` | `~/.config/jev-gmail-labeler/criteria.json` | Your categories and labels ([section 7](#7-write-your-email-criteria)) |
| `state_db` | `~/.local/state/jev-gmail-labeler/state.sqlite3` | The service's memory (sync position, processed emails) |
| `typesafe_api_key_file` | none | File holding the TypeSafe API key; if unset, `TYPESAFE_API_KEY` is used |
| `jev_model` | `jev-latest` | Jev model name. `jev-latest` can change over time; see [Maintenance](#10-maintenance) |
| `jev_timeout_seconds` | `10.0` | Time limit for one Jev request (above 0) |
| `gmail_user` | `me` | Gmail user ID; `me` means the authorized account |
| `pubsub_topic` | none | `projects/<project>/topics/<topic>`; needed by `listen` and `watch start` |
| `pubsub_subscription` | none | `projects/<project>/subscriptions/<name>`; needed by `listen` |
| `max_body_chars` | `8000` | Longest email body sent to Jev (100 to 60000). Longer bodies are cut first, then anonymized |
| `spacy_model` | `en_core_web_md` | Language model used to find personal details |
| `log_level` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `max_attempts` | `5` | Tries per email before the service gives up on it (1 or more) |
| `idle_resync_minutes` | `15` | After this many quiet minutes the service checks Gmail directly, in case a notification was lost (1 or more) |
| `catchup_max_messages` | `200` | Most emails to process when the service has to catch up after a long gap (1 to 1000) |
| `pull_max_messages` | `10` | Most notifications fetched per pull (1 to 100) |
| `pull_timeout_seconds` | `20.0` | How long one pull waits for notifications (1 to 60) |
| `gmail_quota_units_per_minute` | `5000` | Self-imposed Gmail API budget (100 to 6000); Google's limit is 6000 |

The flag names are the key with dashes, for the settings that have one (for example `--jev-model`, `--max-body-chars`); `pubsub_topic` and `pubsub_subscription` use `--topic` and `--subscription`. See each command's `--help`.

Check what the tool will use:

```
jev-gmail-labeler config show
```

This prints the resolved settings as JSON. The API key itself is never shown, only `"set"` or `"not set"`.

## 6. Authorize Gmail

```
jev-gmail-labeler auth
```

A browser window opens. Sign in, accept the scopes, and the tool saves a token to `~/.config/jev-gmail-labeler/token.json` (readable only by you) and prints `Saved token to <path>`.

Options: `--port PORT` (local port for the redirect; default is any free port), `--no-browser` (print the URL instead of opening it), `--credentials-file PATH`, `--token-file PATH`.

Authorize again whenever you change scopes, or after publishing the app. Delete the old token first:

```
rm ~/.config/jev-gmail-labeler/token.json
jev-gmail-labeler auth
```

### On a server with no browser

From your laptop, forward a port to the server, then authorize with that port:

```
ssh -L 8765:localhost:8765 server
jev-gmail-labeler auth --no-browser --port 8765
```

Open the URL it prints in the browser on your laptop. When Google redirects to `localhost:8765`, the tunnel carries it back to the server.

## 7. Write your email criteria

The criteria file tells the tool what categories exist and which Gmail label each one gets. The default path is `~/.config/jev-gmail-labeler/criteria.json`.

Print a starting point, then edit it:

```
jev-gmail-labeler criteria example > ~/.config/jev-gmail-labeler/criteria.json
jev-gmail-labeler criteria validate
```

`validate` prints `OK: <n> categories` and one `<id> -> <label>` line per category, or lists every problem it finds (exit code 2).

### Example

```json
{
  "version": 1,
  "label_prefix": "Jev",
  "min_confidence": 0.5,
  "uncertain_label": "Unsure",
  "categories": [
    {"id": "receipt", "description": "A receipt for a purchase or payment"},
    {"id": "order_shipped", "description": "A notice that a package has shipped", "label": "Shipping"},
    {"id": "delivery_update", "description": "A delay or change to an in-flight delivery", "label": "Shipping"},
    {"id": "magic_link", "description": {"what": "An email with a one-click sign-in link", "not_for": "password resets"}},
    {"id": "personal", "description": "A personal email from an individual, not a company", "label": null}
  ]
}
```

This produces the labels `Jev/Receipt`, `Jev/Shipping`, `Jev/Magic Link` and `Jev/Unsure`. Personal mail is classified but left without a label.

### Fields

Unknown keys are errors.

| Key | Required | Default | Rule |
|---|---|---|---|
| `version` | yes | | Must be `1` |
| `instructions` | no | A built-in question | Text asking Jev to pick the best category. Not empty |
| `label_prefix` | no | `"Jev"` | Prepended as `<prefix>/<label>`. `""` means no prefix. No leading or trailing `/` or spaces |
| `min_confidence` | no | `0.0` | A number from 0 to 1. Answers below it get the `uncertain_label` |
| `uncertain_label` | no | none | Label for low-confidence answers. None means no label |
| `no_match_label` | no | none | Label when Jev says no category fits. None means no label |
| `categories` | yes | | 1 to 254 categories |
| `categories[].id` | yes | | Lowercase letters, digits and `_`, starting with a letter, up to 64 characters. Unique |
| `categories[].description` | no | none | Tells Jev what the category means: text, or a JSON object or list. Sent as is |
| `categories[].label` | no | made from `id` | Absent: `order_shipped` becomes `Order Shipped`. `null`: classify but never label |

Label rules: each label is 1 to 200 characters, has no `//`, and does not start or end with `/` or a space. Several categories may share a label. A full label name may not be a Gmail system label (`INBOX`, `SENT`, `DRAFT`, `SPAM`, `TRASH`, `UNREAD`, `STARRED`, `IMPORTANT`, `CHAT`) or start with `CATEGORY_`.

### How labels are applied

- Labels are created in Gmail the first time they are needed, along with any parent label (`Jev` for `Jev/Receipt`). Matching ignores upper and lower case.
- Jev always has one extra hidden option, `_none` ("None of the other categories fit this email"). If it wins, the email gets `no_match_label` (or no label if that is unset).
- If Jev's best answer has confidence below `min_confidence`, the email gets `uncertain_label` instead.
- Otherwise the email gets the label of the winning category.
- Emails that already carry one of your labels are skipped, unless you pass `--force`.

### Tips for good results

- Write descriptions that tell categories apart. When two categories get confused, use an object with `what` and `not_for` keys, as `magic_link` does above.
- The order of categories can nudge Jev's answers. Try reordering and compare.
- Start with a dry run on 20 or so emails ([next section](#8-run-it-manually)) and adjust before applying.
- If the service is running, restart it after editing: `systemctl --user restart jev-gmail-labeler`.

## 8. Run it manually

`label` processes emails you pick. By default it is a **dry run**: it classifies and reports, but changes nothing in Gmail and creates no labels.

Choose emails with one of:

- `--count N`: the newest N emails in your inbox (1 to 1000)
- `--query Q`: a Gmail search, up to 100 results. Combine with `--count` to cap the number
- `--id ID`: one specific message ID; repeat for more. Cannot be combined with the others

```
jev-gmail-labeler label --count 20
jev-gmail-labeler label --query "from:shop.example newer_than:7d" --apply
jev-gmail-labeler label --id 18c2f1a9b3d4e5f6 --id 18c2f1a9b3d4e5f7
```

Other flags:

- `--apply`: really apply the labels
- `--force`: also reclassify emails that already have one of your labels. With `--apply`, it removes their other labels from your criteria file
- `--output PATH`: write results to a file instead of stdout
- `--format {json,csv}`: output format. If omitted, it follows the file extension (`.csv` is CSV, anything else is JSON)
- `--criteria-file`, `--typesafe-api-key-file`, `--jev-model`, `--max-body-chars`: override the matching config settings

The summary goes to stderr, for example:

```
Processed 12 emails: 9 labelled, 0 would label, 2 no label, 1 skipped, 0 errors -> results.csv
```

### Results

JSON output is a list with one object per email. A shortened example:

```json
{
  "message_id": "18c2f1a9b3d4e5f6",
  "sender": "Shop <orders@shop.example>",
  "subject": "Your receipt",
  "status": "would_label",
  "classification": {
    "category": "receipt",
    "confidence": 0.97,
    "model": "jev-1.13.0",
    "input_tokens": 412,
    "cost_usd": 0.0000173
  },
  "decision": {"category": "receipt", "reason": "matched", "label_name": "Jev/Receipt"},
  "applied": false,
  "error": null
}
```

`status` is one of `labelled`, `would_label` (dry run), `no_label`, `skipped` (already labelled, or the message is gone) or `error`.

CSV columns: `processed_at`, `message_id`, `thread_id`, `date`, `sender`, `subject`, `status`, `skip_reason`, `category`, `confidence`, `decision_reason`, `label_name`, `label_id`, `applied`, `model`, `request_id`, `input_tokens`, `cost_usd`, `elapsed_ms`, `probabilities`, `error`.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Unexpected error |
| 2 | Bad usage, config or criteria file |
| 3 | Authorization failed (Google token, or TypeSafe key rejected) |
| 4 | `label` finished but at least one email ended in `error` |
| 130 | Interrupted (Ctrl+C) |

### Cost

Jev charges about $0.042 per million input tokens, and output is free. A typical email is a few hundred to a couple of thousand tokens, so one email costs a fraction of a cent. The `cost_usd` field shows the exact figure for each email.

## 9. Run it as a service

`listen` keeps running, waits for Gmail notifications and labels each new inbox email. Only mail arriving in the **inbox** is processed.

Before installing the unit, check that the setup works by running it in the foreground with `--dry-run` (classify, but never label):

```
jev-gmail-labeler listen --dry-run
```

Stop it with Ctrl+C. Then install it as a systemd **user** unit. The unit file is at `deploy/systemd/jev-gmail-labeler.service` in the repository:

```
mkdir -p ~/.config/systemd/user
curl -fsSL https://raw.githubusercontent.com/1npo/jev-gmail-labeler/main/deploy/systemd/jev-gmail-labeler.service \
  -o ~/.config/systemd/user/jev-gmail-labeler.service
systemctl --user daemon-reload
systemctl --user enable --now jev-gmail-labeler
loginctl enable-linger $USER
```

`enable-linger` lets the service run when you are not logged in. The unit reads `TYPESAFE_API_KEY` from `~/.config/jev-gmail-labeler/env` and everything else from `config.json`. The service is not restarted after exit codes 2 or 3, because those need you to fix something (config, criteria or login). Look at the log to see what.

Follow the log:

```
journalctl --user -u jev-gmail-labeler -f
```

### What the service does

- **Watch.** Gmail only sends notifications while a "watch" is active, and a watch lasts about a week. The service starts it at launch and renews it every 24 hours. You do not need a timer.
- **Notifications are triggers.** Each one just tells the service to ask Gmail what changed since its last known position (stored as a "history cursor"). Duplicate or late notifications are harmless.
- **Catch-up.** If the service was off for so long that Gmail no longer has that history, it looks at recent inbox mail (up to `catchup_max_messages`) and starts again from now.
- **Quiet checks.** After `idle_resync_minutes` without notifications, it checks Gmail directly in case one was dropped.
- **Retries.** If one email fails for a temporary reason (rate limit, network), it is retried later with growing delays, up to `max_attempts`. One bad email never stops the others.
- **No double work.** Processed message IDs are kept in the state database, `~/.local/state/jev-gmail-labeler/state.sqlite3` by default. Entries older than 30 days are removed.
- **Shutdown.** `systemctl --user stop` lets the current email finish, then exits.

### Other watch commands

```
jev-gmail-labeler watch start     # start or renew the watch now (needs pubsub_topic)
jev-gmail-labeler watch status    # sync position, watch expiry, last sync (JSON)
jev-gmail-labeler watch stop      # stop Gmail sending notifications
```

### Stop and upgrade

```
systemctl --user stop jev-gmail-labeler
jev-gmail-labeler watch stop
```

To upgrade:

```
uv tool upgrade jev-gmail-labeler
systemctl --user restart jev-gmail-labeler
```

## 10. Maintenance

- **Re-authorize.** Delete `token.json` and run `jev-gmail-labeler auth`. Then restart the service.
- **Rotate the TypeSafe API key.** Edit `~/.config/jev-gmail-labeler/env` (or the key file) and restart the service.
- **Reset state.** Stop the service, delete the state database, run `jev-gmail-labeler watch start`, and start the service again. It will begin from the present and not reprocess old mail.
- **Pin the Jev model.** `jev-latest` can change without notice. Once you are happy with your `min_confidence`, set `jev_model` to a specific version such as `jev-1.13.0` (the `model` field in `label` output shows the version in use).

## 11. Troubleshooting

| Problem | Likely cause and fix |
|---|---|
| No notifications arrive | `gmail-api-push@system.gserviceaccount.com` is missing the Publisher role **on the topic** ([4.5](#45-let-gmail-publish-to-the-topic)), or `pubsub_topic` does not match the topic you granted. Run `watch status` to see whether a watch is active |
| `invalid_grant` about once a week | The OAuth app is still in Testing status, so the token expires after 7 days. Publish it ([4.7](#47-publish-the-app)), delete the token and run `auth` |
| Log says `Authorization failed ... Run jev-gmail-labeler auth` (exit 3) | Token missing, revoked, or missing the `pubsub` scope. Delete `token.json` and run `auth` again |
| Log says the TypeSafe API key was rejected (exit 3) | Wrong or revoked key. Check `TYPESAFE_API_KEY` or `typesafe_api_key_file` with `config show` |
| Service stopped working after a month of idleness | The subscription was deleted. Recreate it with `--expiration-period=never` ([4.6](#46-create-the-subscription)) |
| `Cannot read subscription ...` | Wrong `pubsub_subscription`, or the signed-in account lacks access to it |
| `spaCy model 'en_core_web_md' is not installed` | Reinstall with `uv tool install --reinstall git+https://github.com/1npo/jev-gmail-labeler.git`. If you set `spacy_model` to another model, install it as shown in [section 3](#use-the-larger-language-model) |
| Many `429` or rate-limit messages | Too many Gmail calls at once. Lower `gmail_quota_units_per_minute`; temporary errors are retried automatically |
| A criteria error at start (exit 2) | Run `jev-gmail-labeler criteria validate` for the list of problems |

## 12. Uninstall

```
jev-gmail-labeler watch stop
systemctl --user disable --now jev-gmail-labeler
rm ~/.config/systemd/user/jev-gmail-labeler.service
systemctl --user daemon-reload
uv tool uninstall jev-gmail-labeler
rm -r ~/.config/jev-gmail-labeler ~/.local/state/jev-gmail-labeler
```

Then revoke access at [myaccount.google.com/permissions](https://myaccount.google.com/permissions). Optionally delete the Pub/Sub topic and subscription, or the whole Google Cloud project. Labels the tool created stay in Gmail until you delete them.
