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

This section assumes you have never used Google Cloud Platform (GCP) before. You do it once, and it takes about 30 minutes. Every step gives the click-by-click **Console** (web) route. Where a command-line equivalent exists, it also gives the `gcloud` version. You can mix the two.

Console menu names change now and then. If a button is not where this guide says, use the search bar at the top of the Console to find the page by name.

### 4.1 What you are building

Gmail cannot call your homelab directly. Instead, the tool uses these pieces, all inside one Google Cloud **project** (a container that holds your settings, APIs and billing):

| Piece | What it is for | Needed for |
|---|---|---|
| **Project** | The container for everything below | Everything |
| **Gmail API** | Lets the tool read mail and apply labels | `label` and `listen` |
| **OAuth consent screen** | The "this app wants access to your Gmail" page you will click through | `label` and `listen` |
| **OAuth client** | An ID file (`credentials.json`) that lets the tool ask you for permission. It acts as you; there is no separate service account | `label` and `listen` |
| **Cloud Pub/Sub API** | Google's message queue | `listen` only |
| **Topic** | The mailbox Gmail drops "you have new mail" notices into | `listen` only |
| **Subscription** | The line the service waits in to collect those notices from the topic | `listen` only |

If you only plan to use `label` by hand, do 4.2, 4.3, 4.5, 4.6 and 4.10, skip billing (4.4) and enable only the Gmail API. If you want the always-on service, do everything.

**Cost.** Gmail API and the consent screen are free. Pub/Sub gives the first 10 GiB of traffic per month free, and a personal mailbox sends a few kilobytes a day. You must still attach a billing account (a credit card) to the project to use Pub/Sub. As long as you stay inside the free tier you are not charged. Section 4.4 shows how to set a budget alert as a safety net.

**Names you will choose.** Pick these now and use them as-is throughout. The rest of the guide assumes them:

| Thing | Value used in this guide |
|---|---|
| Project ID | `YOUR_PROJECT_ID` (you invent it in 4.3; for example `jev-labeler-nick42`) |
| Topic ID | `gmail-labeler` |
| Subscription ID | `gmail-labeler-sub` |

### 4.2 Optional: install the gcloud CLI

Everything can be done in the web Console, so skip this if you prefer clicking. The `gcloud` command-line tool is faster and makes steps easy to repeat or verify. It is also the easiest way to confirm the setup at the end (4.11).

1. Install it by following [cloud.google.com/sdk/docs/install](https://cloud.google.com/sdk/docs/install). On Debian or Ubuntu, use the apt instructions on that page. On other Linux systems, the tarball install works.
2. Check it:

   ```
   gcloud --version
   ```

3. Sign in. This prints a URL. Open it in a browser, choose your Google account, approve, and paste the code back (or, with a browser on the same machine, it finishes by itself):

   ```
   gcloud auth login
   ```

   On a server with no browser, use `gcloud auth login --no-launch-browser` and open the URL on your laptop.

Use the same Google account for `gcloud`, the Console and Gmail unless you have a reason not to. The account that creates the project becomes its **Owner**, which gives it all the rights the tool needs on Pub/Sub. The tool does not use a service account; it reaches Pub/Sub with your own login token.

### 4.3 Create a project

Everything lives in one project, so create a fresh one rather than reusing something else.

**Console:**

1. Go to [console.cloud.google.com](https://console.cloud.google.com/) and sign in. If this is your first visit, accept the terms of service. (If it offers a "free trial" with credit, you can take it, but it is not required.)
2. Click the **project picker** at the top left of the page. It sits next to the "Google Cloud" logo and shows either a project name or "Select a project".
3. In the dialog, click **New project**.
4. **Project name:** `jev-gmail-labeler` (a display name, you can change it later).
5. **Project ID:** below the name, Google suggests an ID. Click **Edit** to change it if you like. The ID must be 6 to 30 characters of lowercase letters, digits and hyphens, start with a letter, and be unique across all of Google Cloud. **You cannot change it later.** Write it down; this is your `YOUR_PROJECT_ID`.
6. Leave **Location / Organization** as "No organization" (normal for a personal account).
7. Click **Create**. Wait for the bell icon notification to say the project was created, then use the project picker to **select** it. The project name must show at the top of the page before you continue.

**gcloud:**

```
gcloud projects create YOUR_PROJECT_ID --name="jev-gmail-labeler"
gcloud config set project YOUR_PROJECT_ID
```

If it says the ID is already taken, add some digits to it and try again. The second command makes `gcloud` use this project by default. The remaining `gcloud` commands in this guide rely on that.

### 4.4 Set up billing and enable the APIs

**Link a billing account** (skip if you only use `label` manually, and enable only the Gmail API below).

Console:

1. Open the menu (☰, top left), then **Billing**.
2. If you have no billing account, click **Create account** (or **Link a billing account**) and follow the prompts: country, name, address and a payment card. Google may place a small temporary authorization hold on the card to verify it.
3. Make sure the billing account is linked to your new project. Open **Billing → Account management** (or **Billing → My projects**) and check `jev-gmail-labeler` is listed. If it is not, click the three-dot menu next to it, choose **Change billing**, and pick your account.

**Add a budget alert** so a mistake can never surprise you:

1. **Billing → Budgets & alerts → Create budget**.
2. Name `jev-labeler`, scope: your project, amount: for example **$5** per month.
3. Keep the default alert thresholds (50%, 90%, 100%) and email notifications. Save.

A budget only alerts you. It does not stop spending.

gcloud (list your billing accounts, then link one):

```
gcloud billing accounts list
gcloud billing projects link YOUR_PROJECT_ID --billing-account=XXXXXX-XXXXXX-XXXXXX
```

(Older `gcloud` versions need `gcloud beta billing ...`.)

**Enable the APIs.** An API is off in a new project until you turn it on.

Console:

1. Menu → **APIs & Services → Library**.
2. Search for **Gmail API**, click it, then click **Enable**.
3. Go back to the Library, search for **Cloud Pub/Sub API**, click it, then **Enable**. (Skip this for manual-only use.)

gcloud:

```
gcloud services enable gmail.googleapis.com pubsub.googleapis.com
```

This can take a minute. If you get an error saying billing must be enabled, finish the billing step above first.

### 4.5 Configure the consent screen

Before Google lets any app ask for permission, the project needs a consent screen: the page you will see when you authorize the tool. It is only ever shown to you.

The Console calls this area **Google Auth Platform**. Menu → **APIs & Services → OAuth consent screen** takes you to the same place. If you see a **Get started** button, click it; the first-time setup is a four-step wizard.

1. **App information.**
   - **App name:** `jev-gmail-labeler`.
   - **User support email:** choose your own address from the drop-down.
   - Click **Next**.
2. **Audience.** Choose **External**. (Internal is only offered to Google Workspace organizations, and is not available on a personal `@gmail.com` account.) Click **Next**.
3. **Contact information.** Enter your email address. Click **Next**.
4. **Finish.** Tick the box to agree to the Google API Services User Data Policy, click **Continue**, then **Create**.

Now add the permissions the tool asks for. These are called **scopes**:

1. In the left menu of Google Auth Platform, click **Data Access**.
2. Click **Add or remove scopes**.
3. A panel opens with a filterable table. Scroll to the bottom and find the box **Manually add scopes**, paste both of these (one per line), and click **Add to table**:

   ```
   https://www.googleapis.com/auth/gmail.modify
   https://www.googleapis.com/auth/pubsub
   ```

   (You can also tick them in the table: "Gmail API … read, compose and send emails" is `gmail.modify`, and "Cloud Pub/Sub API … View and manage Pub/Sub topics and subscriptions" is `pubsub`. They only show in the table once the APIs are enabled.)
4. Click **Update**, then **Save** at the bottom of the Data Access page.

`gmail.modify` lets the tool read mail and change labels. It cannot delete mail permanently or send mail. `pubsub` lets it pull notifications. If a scope does not appear in the table, check that the API from 4.4 is enabled.

Finally, add yourself as a test user (this is what makes sign-in possible until the app is published in 4.10):

1. Click **Audience** in the left menu.
2. Under **Test users**, click **Add users**, enter your Gmail address, and **Save**.

There is no `gcloud` command for the consent screen.

### 4.6 Create the OAuth client

The OAuth client is the credential file that identifies the tool to Google.

**Console:**

1. In Google Auth Platform, click **Clients** in the left menu, then **Create client**. (Older path: **APIs & Services → Credentials → Create credentials → OAuth client ID**.)
2. **Application type:** choose **Desktop app**. This is important. The other types will not work, because the tool receives the login result on a temporary `localhost` address.
3. **Name:** `jev-gmail-labeler` (a label for you only).
4. Click **Create**.
5. A dialog shows a client ID and client secret. Click **Download JSON**. (You can also download it later from the Clients list: click the client's name, then the download icon.)
6. Move the file into place and lock down its permissions. The client secret is sensitive, so keep it out of git and chat. Adjust the first command if your download went elsewhere or the file is on another computer (use `scp` to copy it to the server):

   ```
   mkdir -p ~/.config/jev-gmail-labeler
   mv ~/Downloads/client_secret_*.json ~/.config/jev-gmail-labeler/credentials.json
   chmod 600 ~/.config/jev-gmail-labeler/credentials.json
   ```

The file should be a small JSON document that starts with `{"installed":{"client_id": ...`. If it starts with `{"web":` you chose the wrong application type; delete that client and create a Desktop one.

There is no `gcloud` command for creating a Desktop client.

### 4.7 Create the Pub/Sub topic

(Skip 4.7 to 4.9 if you only use `label` manually.)

The topic is the channel Gmail posts "new mail" notifications to.

**Console:**

1. Menu → **Pub/Sub → Topics**. (Search "Pub/Sub" in the top bar if you cannot find it.)
2. Click **Create topic**.
3. **Topic ID:** `gmail-labeler`.
4. **Untick "Add a default subscription".** You will make your own in 4.9 with different settings.
5. Leave the other options (schema, retention, encryption) alone and click **Create**.

**gcloud:**

```
gcloud pubsub topics create gmail-labeler
```

The full topic name is `projects/YOUR_PROJECT_ID/topics/gmail-labeler`. Replace `YOUR_PROJECT_ID` with your real ID. You will need this exact string in [Configure](#5-configure). In the Console it appears at the top of the topic's page, with a copy button.

### 4.8 Let Gmail publish to the topic

Gmail posts notifications from a Google-owned service account, `gmail-api-push@system.gserviceaccount.com`. It must have the **Pub/Sub Publisher** role on your topic. Without this, you will get no notifications and no error, so do not skip it.

**Console:**

1. **Pub/Sub → Topics**, then click the topic ID `gmail-labeler` to open it.
2. Open the **Permissions** panel. It is on the right side of the page; if it is hidden, click **Show info panel** at the top right. (On some layouts it is a **Permissions** tab instead.)
3. Click **Add principal**.
4. **New principals:** `gmail-api-push@system.gserviceaccount.com`. Type it in full; it will not appear in any drop-down.
5. **Role:** open the drop-down, type `Pub/Sub Publisher`, and select it.
6. Click **Save**. If Console warns that the principal is outside your organization or does not exist, confirm anyway; it is a real Google system account.

**gcloud:**

```
gcloud pubsub topics add-iam-policy-binding gmail-labeler \
  --member=serviceAccount:gmail-api-push@system.gserviceaccount.com \
  --role=roles/pubsub.publisher
```

Check it took effect:

```
gcloud pubsub topics get-iam-policy gmail-labeler
```

The output should contain `roles/pubsub.publisher` with `serviceAccount:gmail-api-push@system.gserviceaccount.com` under `members`.

### 4.9 Create the subscription

The subscription is where the service collects notifications from the topic. Two settings matter:

- `--ack-deadline=300` gives the service five minutes to handle a notification before Pub/Sub sends it again.
- `--expiration-period=never` stops Google deleting the subscription. By default, a subscription with no activity for 31 days is deleted, which would silently stop the service.

**Console:**

1. **Pub/Sub → Subscriptions → Create subscription**.
2. **Subscription ID:** `gmail-labeler-sub`.
3. **Cloud Pub/Sub topic:** click the box and choose `projects/YOUR_PROJECT_ID/topics/gmail-labeler`.
4. **Delivery type:** **Pull**. (Not Push; your homelab has no public address for Google to call.)
5. **Message retention duration:** leave at 7 days.
6. **Expiration period:** choose **Never expire**.
7. **Acknowledgement deadline:** `300` seconds.
8. Leave retry policy as the default ("Retry immediately") and leave dead lettering off. Click **Create**.

**gcloud:**

```
gcloud pubsub subscriptions create gmail-labeler-sub \
  --topic=gmail-labeler \
  --ack-deadline=300 \
  --expiration-period=never
```

The full name is `projects/YOUR_PROJECT_ID/subscriptions/gmail-labeler-sub`.

### 4.10 Publish the app

While the app is in **Testing** status, Google expires your login token after 7 days, and you would have to re-authorize every week. Moving it to **In production** removes that limit. You do not need Google's verification, because you are the only user: unverified apps are allowed up to 100 users.

**Console:**

1. **Google Auth Platform → Audience**.
2. Under **Publishing status**, click **Publish app**.
3. Read the dialog and click **Confirm**. The status changes to **In production**. You will not be asked to submit anything for verification.

The first time you authorize (see [Authorize Gmail](#6-authorize-gmail)), Google shows **"Google hasn't verified this app"**. That is expected, since you wrote the app. Click **Advanced**, then **Go to *app name* (unsafe)**, tick the permission boxes on the next screen (all of them), and click **Continue**.

If you authorized before publishing, or before adding the `pubsub` scope, delete the old token (`rm ~/.config/jev-gmail-labeler/token.json`) and authorize again.

### 4.11 Check your setup

If you installed `gcloud`, these commands confirm each piece exists:

```
gcloud config get-value project
gcloud services list --enabled | grep -E 'gmail|pubsub'
gcloud pubsub topics describe gmail-labeler
gcloud pubsub subscriptions describe gmail-labeler-sub
```

The subscription description should show `ackDeadlineSeconds: 300`, an empty `expirationPolicy: {}` (this means never expire), and `topic: projects/YOUR_PROJECT_ID/topics/gmail-labeler`.

Then finish [Configure](#5-configure) and [Authorize Gmail](#6-authorize-gmail), and test end to end:

```
jev-gmail-labeler watch start
jev-gmail-labeler listen --dry-run
```

Send yourself an email from another account. Within a few seconds the foreground `listen` should report it. If it does not, see the first row of [Troubleshooting](#11-troubleshooting).

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
| `credentials_file` | `~/.config/jev-gmail-labeler/credentials.json` | OAuth client file from step 4.6 |
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

For a larger starting point, copy [`criteria.json`](criteria.json), a full set of 36 categories (orders, money, account security, jobs and more), then trim it to the categories you want:

```
cp docs/criteria.json ~/.config/jev-gmail-labeler/criteria.json
```

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

This produces the labels `Jev/receipt`, `Jev/Shipping`, `Jev/magic_link` and `Jev/Unsure`. Personal mail is classified but left without a label.

### Fields

Unknown keys are errors.

| Key | Required | Default | Rule |
|---|---|---|---|
| `version` | yes | | Must be `1` |
| `instructions` | no | A built-in question | Text asking Jev to pick the best category. Not empty |
| `label_prefix` | no | `""` | Prepended as `<prefix>/<label>`. Empty or absent means no prefix. No leading or trailing `/` or spaces |
| `min_confidence` | no | `0.0` | A number from 0 to 1. Answers below it get the `uncertain_label` |
| `uncertain_label` | no | none | Label for low-confidence answers. None means no label |
| `no_match_label` | no | none | Label when Jev says no category fits. None means no label |
| `categories` | yes | | 1 to 254 categories |
| `categories[].id` | yes | | Lowercase letters, digits and `_`, starting with a letter, up to 64 characters. Unique |
| `categories[].description` | no | none | Tells Jev what the category means: text, or a JSON object or list. Sent as is |
| `categories[].label` | no | the `id`, hyphenated | Absent: the label is the `id` with underscores and spaces replaced by hyphens, eg `order-shipped`. `null`: classify but never label |

Label rules: each label is 1 to 200 characters, has no `//`, and does not start or end with `/` or a space. Several categories may share a label. A full label name may not be a Gmail system label (`INBOX`, `SENT`, `DRAFT`, `SPAM`, `TRASH`, `UNREAD`, `STARRED`, `IMPORTANT`, `CHAT`) or start with `CATEGORY_`.

### How labels are applied

- Labels are created in Gmail the first time they are needed, along with any parent label (`Jev` for `Jev/receipt`, if `label_prefix` is `Jev`). Matching ignores upper and lower case.
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
  "decision": {"category": "receipt", "reason": "matched", "label_name": "receipt"},
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

Stop it with Ctrl+C. Then install it as a systemd **user** unit. The unit file is at `docs/deploy/systemd/jev-gmail-labeler.service` in the repository:

```
mkdir -p ~/.config/systemd/user
curl -fsSL https://raw.githubusercontent.com/1npo/jev-gmail-labeler/main/docs/deploy/systemd/jev-gmail-labeler.service \
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
| No notifications arrive | `gmail-api-push@system.gserviceaccount.com` is missing the Publisher role **on the topic** ([4.8](#48-let-gmail-publish-to-the-topic)), or `pubsub_topic` does not match the topic you granted. Run `watch status` to see whether a watch is active |
| `invalid_grant` about once a week | The OAuth app is still in Testing status, so the token expires after 7 days. Publish it ([4.10](#410-publish-the-app)), delete the token and run `auth` |
| Log says `Authorization failed ... Run jev-gmail-labeler auth` (exit 3) | Token missing, revoked, or missing the `pubsub` scope. Delete `token.json` and run `auth` again |
| Log says the TypeSafe API key was rejected (exit 3) | Wrong or revoked key. Check `TYPESAFE_API_KEY` or `typesafe_api_key_file` with `config show` |
| Service stopped working after a month of idleness | The subscription was deleted. Recreate it with `--expiration-period=never` ([4.9](#49-create-the-subscription)) |
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
