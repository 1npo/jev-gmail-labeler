# jev-gmail-labeler

Label your Gmail messages automatically, as they arrive, using [Jev](docs/ABOUT_JEV.md) (TypeSafe's fast text classifier).

## Content

- [jev-gmail-labeler](#jev-gmail-labeler)
  - [Content](#content)
  - [What it does](#what-it-does)
  - [How it works](#how-it-works)
  - [Requirements](#requirements)
  - [Install](#install)
  - [Quick start](#quick-start)
  - [Usage](#usage)
  - [Documentation](#documentation)
  - [Privacy](#privacy)
  - [Development](#development)
  - [License](#license)

## What it does

- Watches your Gmail inbox and picks up new mail within seconds.
- Replaces personal details in each email body (names, phone numbers and so on) with placeholders.
- Asks Jev which of your categories the email belongs to.
- Applies the Gmail label you chose for that category.

You can also label existing mail by hand, as a dry run first, and save the results as JSON or CSV.

## How it works

Gmail sends a notification to Google Pub/Sub (a message queue) whenever mail arrives. The `listen` command pulls those notifications, fetches each new email, anonymizes it, sends it to Jev, and applies the label.

```
Gmail -> Pub/Sub -> listener -> anonymize -> Jev -> Gmail label
```

Your categories and labels live in a small JSON file that you write.

## Requirements

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- A Google account and a Google Cloud project (free tier is enough)
- A TypeSafe API key
- Linux with systemd, to run it as a background service

## Install

```
uv tool install git+https://github.com/1npo/jev-gmail-labeler.git
```

## Quick start

1. [Set up Google Cloud](docs/USER_GUIDE.md#4-set-up-google-cloud): APIs, OAuth client, topic and subscription.
2. [Configure](docs/USER_GUIDE.md#5-configure) the config file and your TypeSafe API key.
3. [Authorize Gmail](docs/USER_GUIDE.md#6-authorize-gmail): `jev-gmail-labeler auth`.
4. [Write your criteria](docs/USER_GUIDE.md#7-write-your-email-criteria) and check them: `jev-gmail-labeler criteria validate`. [`docs/criteria.json`](docs/criteria.json) is a ready-made example to start from.
5. [Try a dry run](docs/USER_GUIDE.md#8-run-it-manually): `jev-gmail-labeler label --count 20`.
6. [Run it as a service](docs/USER_GUIDE.md#9-run-it-as-a-service) with systemd.

## Usage

```
# Dry run on your 20 newest inbox emails (prints JSON, changes nothing)
jev-gmail-labeler label --count 20

# Label matching emails and save a CSV report
jev-gmail-labeler label --query "from:shop.example newer_than:7d" --apply --output report.csv

# Check your criteria file
jev-gmail-labeler criteria validate

# Run the listener in the foreground
jev-gmail-labeler listen
```

Run `jev-gmail-labeler <command> --help` for all options.

## Documentation

- [User guide](docs/USER_GUIDE.md): setup, configuration, criteria, running, troubleshooting
- [Example criteria](docs/criteria.json): a full set of 36 categories to copy and edit
- [About Jev](docs/ABOUT_JEV.md): what Jev is and how it works

## Privacy

- TypeSafe receives the email headers (From, To, Reply-To, Date, Subject) and the anonymized, length-limited body. Anonymization only covers the body, so the headers are sent as they are.
- Google sees only your label changes and normal Gmail API calls.
- Your Google token and API key stay on your machine. Logs never contain email bodies or secrets.

## Development

```
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## License

This project is licensed under the [MIT License](LICENSE).