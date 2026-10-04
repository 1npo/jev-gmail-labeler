# jev-gmail-labeler rewrite plan

Status: approved design, ready for execution. Planned 2026-10-03.

This plan is executed by **3 sessions**, run in order. Each session reads this whole file, then does only its own section (§S1, §S2 or §S3). Every design decision is made here. If something is not covered, choose the simplest option consistent with this plan, and mention it in your final report. Do not redesign.

---

## 0. Ground rules (all sessions)

1. **Never open, print, copy, or quote anything inside `workspace/`**, `token.json`, `credentials.json`, or any `*.sqlite3` file. They hold secrets and personal email. `workspace/` stays in `.gitignore` (`workspace/**`, `token.json`, `credentials.json` lines must remain).
2. **Environment.** Sessions run with the `claude` CLI on the Linux host `diatom`, from the repo root. Use the project's `.venv` through `uv` (`uv sync`, `uv run ...`). Never delete or recreate `.venv` by hand.
3. **Preflight** (start of each session):
   - Run `git status`. If git reports "dubious ownership" or another permission problem, stop and ask the user to fix it. Do not change the global git config yourself.
   - `git config user.email` must be set. If it isn't, stop and ask the user. Don't set an identity yourself.
   - The working tree must be clean and on `main`. If it isn't, stop and ask the user. Don't commit, stash or discard their changes.
   - Run `uv --version`. If a later `uv lock` or `uv sync` fails because of the lockfile version, stop and ask the user to run `uv self update`.
4. Tests must never touch disk or network (§8). Never log or print email bodies or secrets.
5. Code style: match `pyproject.toml` ruff settings (single quotes, line length 90). Type-hint all public functions. Add a short docstring on each public class and function. Use `from __future__ import annotations` only where needed.
6. **Git:** use one branch per session, and atomic commits at the boundaries listed. Commit messages are short, imperative, sentence case (e.g. `Add Gmail message parser`). Merge to `main` only after every "Done when" check passes. Use `git checkout main && git merge --no-ff <branch> -m "Merge <branch>"`, then `git push origin main`. If the push fails (e.g. the SSH host alias `github-1npo` is missing on this machine), don't try other remotes or auth methods. Report the exact command for the user to run.

---

## 1. Decisions (assumptions made for you)

| # | Decision |
|---|---|
| D1 | Package `jev_gmail_labeler` in `src/`. Google subpackage is `google_api`, TypeSafe subpackage is `typesafe_api`. These names avoid shadowing the `google` namespace and the `typesafe_sdk` library. |
| D2 | Everything is **synchronous**. Use the sync `typesafe_sdk.TypeSafeClient` and a **unary Pub/Sub pull** loop (`SubscriberClient.pull`), not streaming pull. Volume is ≤1 notification/sec per mailbox (a Gmail limit), so the simple, single-threaded, testable loop wins. |
| D3 | One OAuth flow for everything. A *Desktop app* OAuth client plus a user token with scopes `gmail.modify` and `pubsub`. Pub/Sub is called with the same user credentials. There is no service account. Existing tokens lack the `pubsub` scope, so the user must re-run `auth`. |
| D4 | The service renews the Gmail watch itself (at startup, then every 24 h). There is **no separate timer unit**. One systemd **user** unit. |
| D5 | Only messages added to **INBOX** are processed by the service. |
| D6 | State (history cursor, watch info, per-message status) lives in SQLite at `state_db`. The manual `label` command does not use the state DB. |
| D7 | Idempotency has two layers. (a) The service skips message IDs already in the state DB. (b) The pipeline skips any message that already carries a label managed by the criteria file, unless `--force` is given. |
| D8 | The criteria file defines one Jev **Choice** question. The PoC's extra `from_government` Noul is dropped. A reserved no-match option `_none` is always appended. |
| D9 | Anonymization uses Presidio with the **replace** operator, which gives placeholders like `<PERSON>` (the PoC used `redact`). Only the body is anonymized, matching the PoC. The body is truncated to `max_body_chars` (default 8000) **before** anonymization. |
| D10 | The spaCy model `en_core_web_md` 3.8.0 (about 33 MB) is a direct-URL dependency, so installs work offline after setup. It is a middle ground between size and name-detection accuracy. Users who want the large model can add it at install time with `uv tool install --with "en-core-web-lg @ <wheel URL>" ...` and set `spacy_model` to `en_core_web_lg`. |
| D11 | The `report`, `get-emails`, `anonymize-emails` and `classify-emails` commands are removed. `label --output x.csv` replaces `report`. `pandas` and `ipython` are dropped. |
| D12 | Config precedence, lowest to highest: built-in defaults < env vars `JEV_LABELER_*` < config file < `--config-json` < individual CLI flags. |
| D13 | The TypeSafe API key never appears in config JSON or CLI flags. It comes from `typesafe_api_key_file` (if set) or else env `TYPESAFE_API_KEY`. |
| D14 | Default paths come from `platformdirs`. Config dir is `user_config_path('jev-gmail-labeler')` (Linux: `~/.config/jev-gmail-labeler`). State dir is `user_state_path('jev-gmail-labeler')` (Linux: `~/.local/state/jev-gmail-labeler`). |
| D15 | Gmail labels are named `<label_prefix>/<label>` (default prefix `Jev`). They are created lazily, the first time they are applied, along with any missing parent labels. Label lookup is case-insensitive. |
| D16 | The CLI entry point is `jev-gmail-labeler` (the PoC's `jev-email-labeler` was a typo). The version is bumped to `0.2.0`. |
| D17 | The old top-level package, `main.py` and `_version.py` are deleted in S1. The old code remains in git history. |
| D18 | Gmail retries use googleapiclient's built-in `request.execute(num_retries=5)`. It retries 429, 5xx, rate-limit 403s and socket errors with exponential backoff. A token-bucket `QuotaLimiter` (default 5000 units/min, under Google's 6000/min per-user limit) paces calls. |
| D19 | TypeSafe retries use `RetryPolicy(max_retries=4, timeout=60.0)`. The default model is `jev-latest`. Pricing is $0.042 per million input tokens, and output is free. |

---

## 2. Architecture

### 2.1 Target tree

```
pyproject.toml  uv.lock  README.md  .gitignore  .python-version
deploy/systemd/jev-gmail-labeler.service      (S2)
docs/user-guide.md  docs/ABOUT_JEV.md          (S3)
planning/PLAN.md  planning/prompts/*.md        (S3 moves prompts/)
src/jev_gmail_labeler/
  __init__.py          __version__ re-export
  __main__.py          python -m jev_gmail_labeler             (S2)
  _version.py          __version__ = '0.2.0'
  errors.py            exception hierarchy
  models.py            pipeline dataclasses + JSON (de)serialization
  files.py             ALL disk I/O helpers (single mock point)
  config.py            AppConfig, load_config, precedence
  criteria.py          Criteria, Category, parse/load, EXAMPLE_CRITERIA
  logging_setup.py     configure_logging, secret redaction
  output.py            results -> JSON / CSV text, write_results
  anonymize.py         Anonymizer (Presidio, lazy)
  pipeline.py          decide(), Pipeline
  state.py             StateStore (SQLite)                     (S2)
  service.py           LabelerService (listener loop), renew_watch (S2)
  app.py               factories wiring config -> objects       (S2)
  cli.py               argparse UI, exit codes                  (S1 stub, S2 full)
  google_api/
    __init__.py
    auth.py            SCOPES, load_credentials, run_consent_flow
    quota.py           QuotaLimiter, COSTS
    message_parser.py  parse_message, html_to_text, body extraction
    gmail.py           GmailClient + small dataclasses
    labels.py          LabelManager
    pubsub.py          PubSubPuller, parse_notification       (S2)
  typesafe_api/
    __init__.py
    classifier.py      JevClassifier, build_questions, cost
tests/                 mirrors src (unique file names; importlib mode)
  conftest.py          guards + shared fixtures
  test_guards.py test_models.py test_files.py test_config.py test_criteria.py
  test_logging_setup.py test_output.py test_anonymize.py test_pipeline.py test_errors.py
  test_state.py test_service.py test_app.py test_cli.py test_main.py   (S2)
  google_api/test_auth.py test_quota.py test_message_parser.py test_gmail_client.py
             test_labels.py test_pubsub.py(S2)
  typesafe_api/test_classifier.py
```

### 2.2 Existing files

| Path | Action | Session |
|---|---|---|
| `jev_gmail_labeler/` (all, incl. `lib/`, `__pycache__`) | delete (rewritten from scratch in `src/`) | S1 |
| `main.py`, `_version.py` (root) | delete | S1 |
| `pyproject.toml`, `.gitignore` | rewrite / extend | S1 |
| `uv.lock` | regenerate via uv | S1 |
| `sandbox/` | delete | S2 |
| `ABOUT_JEV.md` | `git mv` to `docs/ABOUT_JEV.md` | S3 |
| `prompts/` | `git mv` to `planning/prompts/` | S3 |
| `README.md` | rewrite | S3 |
| `workspace/`, root `token.json` | untouched, stay ignored | — |

### 2.3 Module dependencies (arrows = imports)

```
cli -> app, config, criteria, output, logging_setup, errors, service(renew_watch), state
app -> config, criteria, anonymize, pipeline, service, state, google_api.*, typesafe_api.*
service -> pipeline, state, google_api.gmail, google_api.pubsub, models, errors
pipeline -> models, criteria, anonymize, google_api.gmail, google_api.labels, typesafe_api.classifier, errors
google_api.gmail -> google_api.message_parser, google_api.quota, models, errors
typesafe_api.classifier -> models, criteria, errors
config/criteria/state/google_api.auth -> files, errors
```
`models`, `errors`, `files` import nothing from the package.

---

## 3. Shared interfaces

### 3.1 `errors.py`

```python
class LabelerError(Exception):
    transient: bool = False            # True = retry later may succeed
    def __init__(self, message: str, *, transient: bool | None = None): ...
class ConfigError(LabelerError)        # CLI exit 2
class CriteriaError(ConfigError):
    errors: list[str]                  # __init__(errors: list[str], source: str); str() = f'{source}:\n  ' + '\n  '.join(errors)
class AuthError(LabelerError)          # CLI exit 3 (Google token or TypeSafe key)
class GmailError(LabelerError):
    status: int | None                 # __init__(message, *, status=None, transient=None)
class MessageNotFoundError(GmailError)
class HistoryExpiredError(GmailError)
class PubSubError(LabelerError)
class ClassificationError(LabelerError)
class AnonymizationError(LabelerError)
```

### 3.2 `models.py`

All models are `@dataclass(frozen=True, slots=True)`. Every model has `to_dict() -> dict[str, Any]` (JSON-safe; enums become their `.value`) and `@classmethod from_dict(cls, data: Mapping[str, Any])`. A round-trip test is required for each. Lists are stored as `tuple` and serialized as lists.

```python
class PipelineStatus(StrEnum): LABELLED='labelled'; WOULD_LABEL='would_label'; NO_LABEL='no_label'; SKIPPED='skipped'; ERROR='error'
class DecisionReason(StrEnum): MATCHED='matched'; NO_MATCH='no_match'; LOW_CONFIDENCE='low_confidence'
class SkipReason(StrEnum): ALREADY_LABELLED='already_labelled'; NOT_FOUND='not_found'

EmailMessage: id: str; thread_id: str; history_id: str; internal_date_ms: int; label_ids: tuple[str, ...];
              sender: str; to: str; cc: str; reply_to: str; date: str; subject: str; snippet: str;
              body_text: str           # cleaned plain text (see §7.4)
AnonymizedEmail: id: str; sender: str; to: str; reply_to: str; date: str; subject: str;
              body: str; body_truncated: bool
    def to_jev_state(self) -> dict[str, str]:
        # {'from','to','reply_to','date','subject','body'} exactly these keys
ClassificationResult: message_id: str; category: str; confidence: float;
              probabilities: dict[str, float]; model: str; request_id: str | None; input_tokens: int | None;
              cost_usd: float | None; elapsed_ms: float
LabelDecision: message_id: str; category: str; reason: DecisionReason; label_name: str | None
PipelineResult: message_id: str; thread_id: str | None; date: str | None; sender: str | None;
              subject: str | None; status: PipelineStatus; skip_reason: SkipReason | None;
              classification: ClassificationResult | None; decision: LabelDecision | None;
              applied: bool; label_id: str | None; error: str | None; retryable: bool;
              processed_at: str   # ISO-8601 UTC, e.g. '2026-10-03T12:00:00+00:00'
```

### 3.3 `files.py` (the only module that touches the disk, apart from `sqlite3` in `state.py`)

```python
def read_text(path: Path) -> str                     # with open(path, encoding='utf-8'); lets FileNotFoundError propagate
def read_json(path: Path) -> Any                     # json.JSONDecodeError -> ValueError(f'{path}: invalid JSON: {e}')
def write_text_atomic(path: Path, text: str, *, mode: int | None = None) -> None
    # ensure_dir(path.parent); write to path.with_name(path.name + '.tmp') with
    # open(..., 'w', encoding='utf-8', newline=''); os.chmod(tmp, mode) if mode; os.replace(tmp, path)
def ensure_dir(path: Path, mode: int = 0o700) -> None   # path.mkdir(parents=True, exist_ok=True, mode=mode)
```

---

## 4. Email criteria JSON

### 4.1 Schema (version 1)

| Key | Type | Required | Default | Rule |
|---|---|---|---|---|
| `version` | int | yes | — | must be `1` |
| `instructions` | string | no | `DEFAULT_INSTRUCTIONS` | non-empty |
| `label_prefix` | string | no | `"Jev"` | `""` allowed (no prefix); no leading/trailing `/` or whitespace |
| `min_confidence` | number | no | `0.0` | 0 ≤ x ≤ 1 |
| `uncertain_label` | string \| null | no | null | label used when confidence < `min_confidence` |
| `no_match_label` | string \| null | no | null | label used when Jev picks `_none` |
| `categories` | array | yes | — | 1–254 items |
| `categories[].id` | string | yes | — | regex `^[a-z][a-z0-9_]{0,63}$`, unique |
| `categories[].description` | string \| object \| array \| null | no | null | if string: non-empty. Sent to Jev as-is |
| `categories[].label` | string \| null | no | derived | absent → derived from id (`order_shipped` → `Order Shipped`); `null` → classify but never label |

- Unknown keys at any level are errors.
- Every label value (`label`, `uncertain_label`, `no_match_label`) must be non-empty and ≤ 200 chars, contain no `//`, and not start or end with `/` or whitespace.
- The full label name is `f'{label_prefix}/{label}'` if the prefix is set, else `label`. It must not equal a Gmail system label, compared case-insensitively: `INBOX SENT DRAFT SPAM TRASH UNREAD STARRED IMPORTANT CHAT`. It must not start with `CATEGORY_`.
- Several categories may share a label.

```python
DEFAULT_INSTRUCTIONS = ('Which category best describes this email? Judge by its main purpose. '
    '`subject` and `body` matter most. Personal details in `body` are replaced by placeholders such as <PERSON>.')
NO_MATCH_ID = '_none'
NO_MATCH_DESCRIPTION = 'None of the other categories fit this email.'
```

### 4.2 Example (also documented in the user guide)

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
Resulting labels: `Jev/Receipt`, `Jev/Shipping`, `Jev/Magic Link`, `Jev/Unsure`. Personal mail is classified but left unlabelled.

### 4.3 `criteria.py`

```python
@dataclass(frozen=True, slots=True)
class Category: id: str; description: Any; label_name: str | None   # full name incl. prefix
@dataclass(frozen=True, slots=True)
class Criteria:
    version: int; instructions: str; label_prefix: str; min_confidence: float
    uncertain_label: str | None; no_match_label: str | None    # full names incl. prefix
    categories: tuple[Category, ...]
    def choice_criteria(self) -> dict[str, Any]   # {c.id: c.description for c in categories} + {NO_MATCH_ID: NO_MATCH_DESCRIPTION} (last; file order kept)
    def category(self, category_id: str) -> Category | None
    def managed_label_names(self) -> set[str]     # all non-None category labels + uncertain + no_match
def parse_criteria(data: Any, *, source: str = '<criteria>') -> Criteria   # collects ALL errors, raises CriteriaError
def load_criteria(path: Path) -> Criteria
    # FileNotFoundError -> CriteriaError([f'file not found'], str(path)); ValueError(bad JSON) -> CriteriaError([msg], str(path))
EXAMPLE_CRITERIA: dict[str, Any]
```
- Error message format is `<json path>: <problem>`. Examples: `categories[2].id: must match ^[a-z][a-z0-9_]{0,63}$ (got "Order Shipped")`, `categories[5].id: duplicate id "news"`, `version: required`, `extra_key: unknown key`.
- Build `EXAMPLE_CRITERIA` from the `EMAIL_CRITERIA` dict in the pre-rewrite `jev_gmail_labeler/main.py`. Read it with `git show d799b74:jev_gmail_labeler/main.py`.
  - Use `version: 1`, `label_prefix: "Jev"` and `min_confidence: 0.0`.
  - Keep the category order. Omit `label` (derived). Use `description: null` where the PoC had `None`.
  - Drop the `other` category.
  - Fix the typos `instution` → `institution` and `An "friend request"` → `A "friend request"`.
  - `parse_criteria(EXAMPLE_CRITERIA)` must succeed (test it).

### 4.4 Mapping to Jev and Gmail

- One Jev request per email, with one question `'category': Choice(instructions=criteria.instructions, criteria=criteria.choice_criteria())`.
- Decision (`pipeline.decide`), evaluated in this order:
  1. If `category == NO_MATCH_ID`: reason `NO_MATCH`, label = `no_match_label`.
  2. Else, if `confidence < min_confidence`: reason `LOW_CONFIDENCE`, label = `uncertain_label`.
  3. Else: reason `MATCHED`, label = `criteria.category(cat).label_name`.
- If the label is `None`, the status is `no_label` and nothing is applied.
- Missing labels are created on first apply (`LabelManager.ensure`, §7.6). Dry runs never create labels.

---

## 5. Configuration (`config.py`)

### 5.1 Schema

| Key | Type | Default | Env var | CLI flag |
|---|---|---|---|---|
| `credentials_file` | path | `<config_dir>/credentials.json` | `JEV_LABELER_CREDENTIALS_FILE` | `--credentials-file` |
| `token_file` | path | `<config_dir>/token.json` | `JEV_LABELER_TOKEN_FILE` | `--token-file` |
| `criteria_file` | path | `<config_dir>/criteria.json` | `JEV_LABELER_CRITERIA_FILE` | `--criteria-file` |
| `state_db` | path | `<state_dir>/state.sqlite3` | `JEV_LABELER_STATE_DB` | `--state-db` |
| `typesafe_api_key_file` | path \| null | null | `JEV_LABELER_TYPESAFE_API_KEY_FILE` | `--typesafe-api-key-file` |
| `jev_model` | str | `jev-latest` | `JEV_LABELER_JEV_MODEL` | `--jev-model` |
| `jev_timeout_seconds` | float >0 | 10.0 | `JEV_LABELER_JEV_TIMEOUT_SECONDS` | — |
| `gmail_user` | str | `me` | `JEV_LABELER_GMAIL_USER` | — |
| `pubsub_topic` | str \| null | null | `JEV_LABELER_PUBSUB_TOPIC` | `--topic` |
| `pubsub_subscription` | str \| null | null | `JEV_LABELER_PUBSUB_SUBSCRIPTION` | `--subscription` |
| `max_body_chars` | int 100–60000 | 8000 | `JEV_LABELER_MAX_BODY_CHARS` | `--max-body-chars` |
| `spacy_model` | str | `en_core_web_md` | `JEV_LABELER_SPACY_MODEL` | — |
| `log_level` | `DEBUG\|INFO\|WARNING\|ERROR` | `INFO` | `JEV_LABELER_LOG_LEVEL` | `--log-level` |
| `max_attempts` | int ≥1 | 5 | `JEV_LABELER_MAX_ATTEMPTS` | — |
| `idle_resync_minutes` | int ≥1 | 15 | `JEV_LABELER_IDLE_RESYNC_MINUTES` | — |
| `catchup_max_messages` | int 1–1000 | 200 | `JEV_LABELER_CATCHUP_MAX_MESSAGES` | — |
| `pull_max_messages` | int 1–100 | 10 | `JEV_LABELER_PULL_MAX_MESSAGES` | — |
| `pull_timeout_seconds` | float 1–60 | 20.0 | `JEV_LABELER_PULL_TIMEOUT_SECONDS` | — |
| `gmail_quota_units_per_minute` | int 100–6000 | 5000 | `JEV_LABELER_GMAIL_QUOTA_UNITS_PER_MINUTE` | — |

Format rules:
- `pubsub_topic` must match `^projects/[^/]+/topics/[^/]+$`.
- `pubsub_subscription` must match `^projects/[^/]+/subscriptions/[^/]+$`.
- Paths are expanded with `~`. Relative paths in the config file resolve against the config file's directory. Relative paths from env vars, `--config-json` or CLI flags resolve against the CWD.
- Env values are parsed by field type: int, float, path, or str. The literal `null` sets a nullable field to None.
- The key `typesafe_api_key`, if present in any JSON, raises `ConfigError('typesafe_api_key is not allowed in config; use TYPESAFE_API_KEY or typesafe_api_key_file')`.

### 5.2 Precedence and API

Lowest to highest:
1. Built-in defaults
2. `JEV_LABELER_*` env vars
3. Config file: `--config PATH` if given, else `<config_dir>/config.json` if it exists (absence is not an error)
4. `--config-json '<object>'`
5. Individual CLI flags (only flags actually given; parse them with `default=argparse.SUPPRESS`)

```python
@dataclass(frozen=True, slots=True)
class AppConfig: ...fields above...; config_file: Path | None   # which file was loaded, for `config show`
def default_config_dir() -> Path; def default_state_dir() -> Path       # platformdirs
def load_config(*, cli: Mapping[str, Any], config_path: Path | None, config_json: str | None,
                environ: Mapping[str, str]) -> AppConfig
    # merge layers -> validate (collect all errors) -> ConfigError('\n'.join(errors))
def require(config: AppConfig, *keys: str, command: str) -> None
    # ConfigError(f'{key} is required for `{command}`. Set it in the config file, with --flag, or {ENV}.')
def resolve_typesafe_api_key(config: AppConfig, environ: Mapping[str, str]) -> str
    # file (files.read_text().strip()) if typesafe_api_key_file else environ['TYPESAFE_API_KEY'];
    # missing/empty -> ConfigError('Set TYPESAFE_API_KEY or typesafe_api_key_file')
def redacted_dict(config: AppConfig, environ: Mapping[str, str]) -> dict[str, Any]
    # JSON-safe; adds 'typesafe_api_key': 'set' | 'not set' (never the value)
```
Config JSON: `--config` file and `--config-json` must be JSON objects with schema keys. Unknown keys raise `ConfigError(f'{source}: unknown key "{k}"')`.

### 5.3 Secrets

- Secrets are the token file, credentials file and TypeSafe API key.
- Write the token file with mode `0o600`, and its directories with mode `0o700`.
- `logging_setup.register_secret(value)` is called with the API key and the OAuth refresh/access tokens right after they are loaded. The redacting filter replaces them with `***` in every log record.
- Never log the config except via `redacted_dict`. Never log email bodies. Log subjects only at DEBUG.

---

## 6. CLI (`cli.py`)

`main(argv: Sequence[str] | None = None) -> int`. The entry point is `jev-gmail-labeler = "jev_gmail_labeler.cli:main"`. The top-level parser has only `--version` and the subcommands.

Every subcommand takes these **common flags** (put them after the subcommand): `--config PATH`, `--config-json JSON`, `--log-level`, `--credentials-file`, `--token-file`. All config flags use `default=argparse.SUPPRESS`.

| Command | Extra flags | Behaviour |
|---|---|---|
| `auth` | `--port INT` (default 0), `--no-browser` | Runs the OAuth consent flow with `SCOPES` and writes the token file. Prints `Saved token to <path>` to stderr. |
| `label` | selector: `--count N` (1–1000), `--query Q`, `--id ID` (repeatable); `--apply`; `--force`; `--output PATH`; `--format {json,csv}`; `--criteria-file`; `--typesafe-api-key-file`; `--jev-model`; `--max-body-chars` | Runs the pipeline manually (below). |
| `listen` | `--topic`, `--subscription`, `--state-db`, `--criteria-file`, `--typesafe-api-key-file`, `--jev-model`, `--max-body-chars`, `--dry-run` | Runs the service (§7). Needs `pubsub_topic` and `pubsub_subscription`. |
| `watch start` | `--topic`, `--state-db` | `require(config, 'pubsub_topic', command='watch start')`, then `renew_watch(..., force=True)`, then prints the status JSON |
| `watch stop` | `--state-db` | `gmail.stop_watch()` and `state.clear_watch()` |
| `watch status` | `--state-db` | Prints `{"history_cursor":..,"watch_expiration":ISO\|null,"watch_renewed_at":ISO\|null,"last_sync_at":ISO\|null}` |
| `criteria validate` | `--criteria-file` | Prints `OK: <n> categories` and then one `<id> -> <label or (no label)>` line per category. CriteriaError → exit 2. |
| `criteria example` | — | Prints `json.dumps(EXAMPLE_CRITERIA, indent=2)` to stdout |
| `config show` | (common only) | Prints `redacted_dict` as JSON |

**`label` rules:**
- `--id` excludes `--count` and `--query`, otherwise argparse exits with code 2. At least one selector is required.
- `--count` alone uses query `in:inbox`. `--query` alone caps results at 100. Both together mean the query capped at `--count`. Results come back newest first, from `GmailClient.list_message_ids`.
- Without `--apply` it is a dry run: no labels are created or applied, and the status is `would_label`.
- `--force` classifies messages even if they are already labelled. With `--apply` it also removes other managed labels.
- Output: with no `--output`, print a JSON array of `PipelineResult.to_dict()` (indent 2) to stdout. With `--output`, write the file. The format is `--format` if given, else inferred from the extension (`.csv` → csv, anything else → json).
- Always print a summary to stderr: `Processed 12 emails: 9 labelled, 0 would label, 2 no label, 1 skipped, 0 errors` (+ ` -> <path>` when written).

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | unexpected error (`log.exception`) |
| 2 | usage, config or criteria error |
| 3 | auth error (Google token missing, invalid or revoked; TypeSafe key rejected) |
| 4 | `label` finished, but at least one email ended in `error` |
| 130 | interrupted (KeyboardInterrupt) |

**`main` flow:**
1. `configure_logging('INFO')`, so config errors are visible.
2. Parse args.
3. `load_config(cli=<given config flags>, config_path=args.config, config_json=args.config_json, environ=os.environ)`.
4. `configure_logging(config.log_level)`. It must be idempotent: replace the root handlers.
5. Dispatch to `_cmd_<name>(args, config) -> int`. Exceptions map to the exit codes above.

Each error class logs a single plain line, except code 1. For code 3, the line is `Authorization failed: <msg>. Run \`jev-gmail-labeler auth\`` (for Google) or `<msg>` (for TypeSafe).

### `output.py`

```python
CSV_COLUMNS = ['processed_at','message_id','thread_id','date','sender','subject','status','skip_reason',
  'category','confidence','decision_reason','label_name','label_id','applied','model','request_id',
  'input_tokens','cost_usd','elapsed_ms','probabilities','error']   # probabilities = json.dumps(dict)
def results_to_json(results: Sequence[PipelineResult]) -> str
def results_to_csv(results: Sequence[PipelineResult]) -> str      # csv.DictWriter on io.StringIO; None -> ''
def write_results(results, path: Path, fmt: Literal['json','csv']) -> None   # files.write_text_atomic
def summarize(results) -> dict[PipelineStatus, int]; def summary_line(results, path: Path | None) -> str
```

---

## 7. Runtime components

### 7.1 `google_api/auth.py`

```python
SCOPES = ('https://www.googleapis.com/auth/gmail.modify', 'https://www.googleapis.com/auth/pubsub')
def run_consent_flow(credentials_file: Path, token_file: Path, *, port: int = 0, open_browser: bool = True) -> Credentials
    # files.read_json(credentials_file) (FileNotFoundError -> ConfigError with Cloud Console hint)
    # InstalledAppFlow.from_client_config(cfg, SCOPES).run_local_server(port=port, open_browser=open_browser)
    # save_credentials(...)
def load_credentials(token_file: Path) -> Credentials
    # missing file -> AuthError('No token at <path>'); Credentials.from_authorized_user_info(json, SCOPES)
    # if not creds.has_scopes(SCOPES) -> AuthError('Token is missing required scopes')
    # if not creds.valid: if creds.refresh_token: creds.refresh(Request()) + save; except RefreshError -> AuthError
    #                     else AuthError
    # register_secret(creds.token); register_secret(creds.refresh_token)
def save_credentials(creds, token_file: Path) -> None   # files.write_text_atomic(token_file, creds.to_json(), mode=0o600)
```
- Later access-token refreshes in the long-running process happen automatically. googleapiclient's `AuthorizedHttp` handles Gmail, and the gRPC auth plugin handles Pub/Sub.
- `google.auth.exceptions.RefreshError` raised anywhere maps to `AuthError`. The refresh token doesn't change, so it is not re-saved.

### 7.2 `google_api/quota.py`

```python
COSTS = {'messages.get': 20, 'messages.list': 5, 'messages.modify': 5, 'history.list': 2,
         'labels.list': 1, 'labels.create': 5, 'watch': 100, 'stop': 100, 'getProfile': 1}
class QuotaLimiter:
    def __init__(self, units_per_minute: int = 5000, *, clock=time.monotonic, sleep=time.sleep): ...
    def consume(self, units: int) -> None
        # sliding 60 s window: deque[(t, units)]; drop entries older than 60 s;
        # while used + units > budget: sleep(oldest_t + 60 - now) then drop; append
```

### 7.3 `google_api/gmail.py`

```python
@dataclass(frozen=True, slots=True) class Profile: email_address: str; history_id: int
@dataclass(frozen=True, slots=True) class HistoryPage: message_ids: tuple[str, ...]; history_id: int
@dataclass(frozen=True, slots=True) class WatchResponse: history_id: int; expiration_ms: int
@dataclass(frozen=True, slots=True) class GmailLabel: id: str; name: str; type: str
class GmailClient:
    def __init__(self, credentials, *, user_id: str = 'me', quota: QuotaLimiter | None = None,
                 num_retries: int = 5, service=None)
        # service = service or build('gmail', 'v1', credentials=credentials, cache_discovery=False)
    def _execute(self, request, cost_key: str, *, not_found: type[GmailError] = GmailError):
        # quota.consume(COSTS[cost_key]); return request.execute(num_retries=self._num_retries)
        # error mapping: HttpError 401 -> AuthError; 404 -> not_found(msg, status=404, transient=False);
        # 429/5xx -> GmailError(transient=True); other HttpError -> GmailError(transient=False);
        # RefreshError -> AuthError; OSError/httplib2.HttpLib2Error -> GmailError(transient=True)
    def get_profile(self) -> Profile
    def list_message_ids(self, *, query: str | None, max_results: int) -> list[str]
        # messages.list(userId, q, maxResults=min(500, remaining), pageToken) until max_results or no nextPageToken
    def get_message(self, message_id: str) -> EmailMessage   # format='full'; 404 -> MessageNotFoundError
    def list_history(self, start_history_id: int, *, label_id: str = 'INBOX') -> HistoryPage
        # history.list(userId, startHistoryId=str(id), historyTypes=['messageAdded'], labelId, maxResults=500, pageToken)
        # collect messagesAdded[].message.id across all pages, dedupe keeping first-seen order;
        # history_id = int(last page['historyId']); 404 -> HistoryExpiredError
    def watch(self, topic: str, label_ids: Sequence[str] = ('INBOX',)) -> WatchResponse
        # body {'topicName': topic, 'labelIds': [...], 'labelFilterBehavior': 'INCLUDE'}
    def stop_watch(self) -> None
    def list_labels(self) -> list[GmailLabel]
    def create_label(self, name: str) -> GmailLabel   # labelListVisibility 'labelShow', messageListVisibility 'show'
    def modify_labels(self, message_id: str, *, add: Sequence[str], remove: Sequence[str] = ()) -> None
```

### 7.4 `google_api/message_parser.py`

```python
def parse_message(raw: Mapping[str, Any]) -> EmailMessage
def extract_body_text(payload: Mapping[str, Any]) -> str
def html_to_text(html: str) -> str
def decode_header_value(value: str) -> str   # str(make_header(decode_header(v))); on error return v
```
- **Walk the MIME tree.** Skip any part with a `filename` or with `body.attachmentId`. Decode `body.data` as URL-safe base64, adding padding if needed. Use the charset from the part's `Content-Type` header if `codecs.lookup` knows it, else utf-8, with `errors='replace'`.
- **Body source priority.** If any `text/html` parts exist, use `html_to_text('\n'.join(html_parts))`. Else, if any `text/plain` parts exist, clean the joined text with the same cleanup. Else use the message `snippet` (unescaped). Else use `''`.
- **`html_to_text` and cleanup:**
  1. Parse with `BeautifulSoup(html, 'lxml')`.
  2. Decompose `script`, `style`, `head` and `title`.
  3. Call `get_text('\n')`, then `html.unescape` again (some senders double-escape).
  4. Remove the characters `\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u00ad\u034f`, and replace `\u00a0` with a space.
  5. Collapse runs of spaces and tabs to one space, trim spaces around newlines, collapse 3+ newlines to 2, then `strip()`.
- **Headers.** Look up `From`, `To`, `Cc`, `Reply-To`, `Date` and `Subject` case-insensitively and pass each through `decode_header_value`. Missing headers become `''`.
- **Other fields.** `internal_date_ms = int(raw.get('internalDate', 0))` and `history_id = raw.get('historyId', '')`.

### 7.5 `anonymize.py`

```python
class Anonymizer:
    def __init__(self, spacy_model: str = 'en_core_web_md', *, language: str = 'en'): ...   # no heavy work here
    def _engines(self)  # lazy, cached: imports presidio_analyzer / presidio_anonymizer INSIDE the method;
        # NlpEngineProvider(nlp_configuration={'nlp_engine_name':'spacy','models':[{'lang_code':'en','model_name':m}]})
        # AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=['en']); AnonymizerEngine()
        # OSError (model missing) -> ConfigError(f"spaCy model '{m}' is not installed")
    def anonymize_text(self, text: str) -> str
        # '' -> ''; analyzer.analyze(text=text, language='en'); anonymizer.anonymize(text, results,
        # operators={'DEFAULT': OperatorConfig('replace')}).text ; other exceptions -> AnonymizationError
    def anonymize(self, email: EmailMessage, *, max_body_chars: int) -> AnonymizedEmail
        # truncate body_text to max_body_chars FIRST (body_truncated flag), then anonymize_text
```
Set the loggers `presidio-analyzer` and `presidio-anonymizer` to ERROR in `logging_setup`.

### 7.6 `google_api/labels.py`

```python
class LabelManager:
    def __init__(self, gmail: GmailClient): ...     # cache loaded lazily via list_labels(): {name.casefold(): GmailLabel}
    def find_id(self, name: str) -> str | None
    def ensure(self, name: str) -> str
        # existing -> id; else create each missing ancestor ('A', 'A/B' for 'A/B/C') then name;
        # GmailError status 409 -> reload cache once, return find_id(name) or re-raise
    def existing_ids(self, names: Iterable[str]) -> set[str]   # only labels that already exist; never creates
```

### 7.7 `typesafe_api/classifier.py`

```python
QUESTION_ID = 'category'
USD_PER_INPUT_TOKEN = 0.042 / 1_000_000
def build_questions(criteria: Criteria) -> dict[str, Choice]
    # {'category': Choice(instructions=criteria.instructions, criteria=criteria.choice_criteria())}
def cost_usd(input_tokens: int | None) -> float | None
class JevClassifier:
    def __init__(self, criteria: Criteria, *, api_key: str, model: str = 'jev-latest',
                 timeout: float = 10.0, client: TypeSafeClient | None = None)
        # client or TypeSafeClient(api_key=api_key, model=model, timeout=timeout,
        #                          retry=RetryPolicy(max_retries=4, timeout=60.0)); register_secret(api_key)
    def classify(self, email: AnonymizedEmail) -> ClassificationResult
        # t0 = perf_counter(); r = client.system_one(state=email.to_jev_state(), questions=self._questions)
        # a = r.choices['category']; unknown a.choice -> ClassificationError(transient=False)
        # ClassificationResult(category=a.choice, confidence=a.confidence, probabilities=dict(a.probabilities),
        #   model=r.model, request_id=r.request_id, input_tokens=r.usage.input_tokens, cost_usd=..., elapsed_ms=...)
    def close(self) -> None; __enter__/__exit__
```
**Exception mapping** (`from typesafe_sdk import ...`):

| SDK exception | Maps to |
|---|---|
| `TypeSafeAuthenticationError`, `TypeSafePermissionDeniedError` | `AuthError('TypeSafe API key was rejected')` |
| `TypeSafeBadRequestError`, `TypeSafeUnprocessableEntityError` | `ClassificationError(transient=False)` |
| `TypeSafeRateLimitError`, `TypeSafeInternalServerError`, `TypeSafeAPIConnectionError` (incl. timeout), `TypeSafeAPIResponseValidationError` | `ClassificationError(transient=True)` |
| any other `TypeSafeError` | `ClassificationError(transient=False)` |

The message is `f'{type(e).__name__}: {e}'`. SDK exceptions already exclude the key.

### 7.8 `pipeline.py`

```python
def decide(classification: ClassificationResult, criteria: Criteria) -> LabelDecision   # §4.4
class Pipeline:
    def __init__(self, *, gmail: GmailClient, labels: LabelManager, anonymizer: Anonymizer,
                 classifier: JevClassifier, criteria: Criteria, max_body_chars: int,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC))
    def process(self, message_id: str, *, apply: bool, force: bool = False) -> PipelineResult
    def process_many(self, message_ids: Iterable[str], *, apply: bool, force: bool = False) -> Iterator[PipelineResult]
    def close(self) -> None   # closes classifier; __enter__/__exit__
```
`process` steps, in order. The step names are used in error strings.

1. **`fetch`**: `gmail.get_message`. On `MessageNotFoundError` return status `SKIPPED` with `skip_reason=NOT_FOUND`.
2. **`skip_check`**: `managed = labels.existing_ids(criteria.managed_label_names())`. If `managed & set(email.label_ids)` and not `force`, return status `SKIPPED` with `skip_reason=ALREADY_LABELLED`. Do not classify.
3. **`anonymize`**: `anonymizer.anonymize(email, max_body_chars=...)`.
4. **`classify`**: `classifier.classify(anon)`.
5. **`decide`**: `decide(...)`. If `label_name` is None, return status `NO_LABEL`.
6. **`apply`**: if not `apply`, return status `WOULD_LABEL` with `label_id = labels.find_id(name)`. Otherwise:
   - `label_id = labels.ensure(name)`.
   - `remove = (managed & set(email.label_ids)) - {label_id}` if `force`, else `set()`.
   - `gmail.modify_labels(id, add=[label_id], remove=sorted(remove))`.
   - Return status `LABELLED` with `applied=True`.

Error handling:
- `AuthError` and `ConfigError` propagate, because they are fatal.
- Any other exception in a step returns status `ERROR`, with `error=f'{step}: {type(e).__name__}: {e}'` and `retryable = getattr(e, 'transient', False)`.
- Log one INFO line per result: `<id> <status> category=<c> label=<l> confidence=<.2f>`. Log the subject at DEBUG only.

### 7.9 `state.py` (S2)

```sql
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
  message_id TEXT PRIMARY KEY, status TEXT NOT NULL CHECK (status IN ('done','retry','failed')),
  result_status TEXT, category TEXT, label_name TEXT, attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT, next_retry_at REAL, updated_at REAL NOT NULL);
```
KV keys: `history_cursor`, `watch_expiration_ms`, `watch_renewed_at`, `last_sync_at`. Numbers are stored as text.

```python
@dataclass(frozen=True, slots=True)
class MessageRecord: message_id: str; status: str; attempts: int; next_retry_at: float | None; last_error: str | None
class StateStore:
    def __init__(self, path: Path | str, *, clock=time.time)
        # ':memory:' -> sqlite3.connect(':memory:'); else files.ensure_dir(Path(path).parent); sqlite3.connect(str(path))
        # executescript(schema); every write commits
    def get(self, key) -> str | None; def set(self, key, value: str) -> None; def delete(self, key) -> None
    def get_cursor(self) -> int | None; def advance_cursor(self, history_id: int) -> None   # only if None or greater
    def message(self, message_id) -> MessageRecord | None
    def mark_done(self, message_id, *, result_status: str, category: str | None, label_name: str | None) -> None
    def mark_retry(self, message_id, *, attempts: int, error: str, next_retry_at: float) -> None
    def mark_failed(self, message_id, *, attempts: int, error: str) -> None
    def due_retries(self, now: float) -> list[str]    # status='retry' AND next_retry_at <= now, oldest first
    def prune(self, older_than: float) -> int          # delete done/failed rows with updated_at < older_than
    def clear_watch(self) -> None                      # delete watch_expiration_ms, watch_renewed_at
    def close(self) -> None
```

### 7.10 `google_api/pubsub.py` (S2)

```python
@dataclass(frozen=True, slots=True) class PulledMessage: ack_id: str; data: bytes; message_id: str
@dataclass(frozen=True, slots=True) class Notification: email_address: str; history_id: int
def parse_notification(data: bytes) -> Notification | None
    # json.loads(data) -> {'emailAddress': str, 'historyId': int|str}; anything invalid -> None
class PubSubPuller:
    def __init__(self, subscription: str, credentials, *, client=None)   # client or pubsub_v1.SubscriberClient(credentials=credentials)
    def pull(self, max_messages: int, timeout: float) -> list[PulledMessage]
        # client.pull(request={'subscription': s, 'max_messages': n}, timeout=timeout)
        # DeadlineExceeded -> []; Unauthenticated -> AuthError; NotFound/PermissionDenied ->
        # ConfigError(f'Cannot read subscription {s}: ...'); other GoogleAPICallError -> PubSubError(transient=True)
        # RefreshError -> AuthError
    def ack(self, ack_ids: Sequence[str]) -> None    # no-op if empty; client.acknowledge(request={...})
    def nack(self, ack_ids: Sequence[str]) -> None   # client.modify_ack_deadline(request={..., 'ack_deadline_seconds': 0})
    def close(self) -> None
```
The Python client delivers `message.data` already base64-decoded (bytes of JSON).

### 7.11 `service.py` (S2)

```python
WATCH_RENEW_SECONDS = 24 * 3600; WATCH_MIN_REMAINING_SECONDS = 3600; PRUNE_AFTER_SECONDS = 30 * 86400
def renew_watch(gmail: GmailClient, state: StateStore, topic: str, *, now: float, force: bool = False) -> bool
    # renew if force or no expiration/renewed_at or now - renewed_at >= 24h or expiration_ms/1000 - now <= 3600:
    #   resp = gmail.watch(topic); state.set('watch_expiration_ms', ...); state.set('watch_renewed_at', str(now))
    #   if state.get_cursor() is None: state.advance_cursor(resp.history_id)   # seeds the baseline only
    #   log INFO 'Watch renewed until <iso>'; return True. Otherwise return False.
class LabelerService:
    def __init__(self, *, config: AppConfig, gmail: GmailClient, puller: PubSubPuller, pipeline: Pipeline,
                 state: StateStore, dry_run: bool = False, stop_event: threading.Event | None = None,
                 clock=time.time, monotonic=time.monotonic)
    def request_stop(self, *_: object) -> None        # signal handler: sets stop_event
    def run(self) -> None
    def handle_batch(self, batch: Sequence[PulledMessage]) -> None
    def sync(self) -> None
    def catch_up(self) -> None
    def process(self, message_id: str) -> None
    def retry_due(self) -> None
```
**`run()`:**
1. Install `signal.signal(SIGINT/SIGTERM, self.request_stop)`.
2. `state.prune(now - PRUNE_AFTER_SECONDS)`.
3. `self.profile = gmail.get_profile()`.
4. `renew_watch(...)`.
5. `retry_due()`, then `sync()`, then `last_activity = monotonic()`.
6. Loop while the stop event is not set:
   - `pull(...)`. On a transient `PubSubError`: increment `failures`, call `stop_event.wait(min(60, 2**failures))`, then `continue`. On success, set `failures = 0`.
   - If the batch is non-empty, call `handle_batch` and set `last_activity = monotonic()`.
   - Else, if `monotonic() - last_activity >= idle_resync_minutes*60`, call `sync()` and set `last_activity = monotonic()`. Google recommends this periodic `history.list` fallback for dropped notifications.
   - Call `retry_due()` and `renew_watch(...)`.
7. `finally`: close the puller, pipeline and state. Log `Stopped`.

`AuthError` and `ConfigError` propagate, and the CLI exits 3 or 2.

**`handle_batch`:**
1. Parse each message. Drop invalid notifications and those whose `email_address.casefold() != profile.email_address.casefold()`, with a warning.
2. If any valid notification has `history_id > (state.get_cursor() or -1)`:
   - Try `sync()`.
   - On a transient `GmailError`: `nack(all ack_ids)`, `stop_event.wait(5)`, then return.
3. `ack(all ack_ids)`. Duplicate, stale and out-of-order notifications therefore just get acked. All processing is cursor-driven, so a notification is only a trigger.

**`sync()`:**
1. `cursor = state.get_cursor()`. If None: `state.advance_cursor(gmail.get_profile().history_id)`, then return.
2. `page = gmail.list_history(cursor)`. On `HistoryExpiredError`: `catch_up()`, then return.
3. Call `process(mid)` for each id in `page.message_ids`.
4. Only then `state.advance_cursor(page.history_id)` and `state.set('last_sync_at', str(now))`.

A crash mid-way re-syncs from the old cursor, and the state DB prevents double processing.

**`catch_up()`** (history older than Gmail keeps; typically ≥1 week):
1. `profile = gmail.get_profile()` (the new baseline, taken first so there is no gap).
2. `since = float(state.get('last_sync_at') or now - 86400) - 3600`.
3. `ids = gmail.list_message_ids(query=f'in:inbox after:{int(since)}', max_results=config.catchup_max_messages)`.
4. Call `process` on each id oldest first (`reversed(ids)`).
5. `state.advance_cursor(profile.history_id)` and set `last_sync_at`. Log a WARNING with the count.

**`process(mid)`:**
1. Read `rec = state.message(mid)`. Return if `rec.status in ('done','failed')`, or if `rec.status == 'retry'` and `rec.next_retry_at > now`.
2. `r = pipeline.process(mid, apply=not dry_run)`. If `dry_run`, return without writing state.
3. If `r.status is ERROR`:
   - `attempts = (rec.attempts if rec else 0) + 1`.
   - If `r.retryable` and `attempts < config.max_attempts`: `mark_retry(next_retry_at=now + min(3600, 60 * 2 ** (attempts - 1)))`.
   - Else `mark_failed` and log an ERROR.
4. Otherwise `mark_done(result_status=r.status, category, label_name)`.

**Ack deadline:** the docs tell users to create the subscription with `--ack-deadline=300`. If a long sync exceeds it, the redelivered notification is a harmless no-op.

### 7.12 `app.py` (S2)

```python
def build_gmail(config: AppConfig) -> GmailClient   # load_credentials(config.token_file); QuotaLimiter(config.gmail_quota_units_per_minute)
def build_pipeline(config: AppConfig, *, gmail: GmailClient, environ: Mapping[str, str]) -> Pipeline
    # load_criteria, Anonymizer(config.spacy_model), JevClassifier(api_key=resolve_typesafe_api_key(...)), LabelManager
def build_service(config: AppConfig, *, environ: Mapping[str, str], dry_run: bool) -> LabelerService
    # require(config, 'pubsub_topic', 'pubsub_subscription', command='listen'); one Credentials object shared
```

### 7.13 `logging_setup.py`

```python
def configure_logging(level: str, *, environ: Mapping[str, str] = os.environ, stream=sys.stderr) -> None
    # handler -> stream; format '%(levelname)s %(name)s: %(message)s' if 'JOURNAL_STREAM' in environ
    # (journald adds timestamps) else '%(asctime)s %(levelname)s %(name)s: %(message)s'; attaches RedactingFilter
    # quiets: presidio-analyzer, presidio-anonymizer -> ERROR; googleapiclient.discovery_cache -> ERROR; typesafe_sdk -> WARNING
def register_secret(value: str | None) -> None    # ignores None/'' and values shorter than 8 chars
class RedactingFilter(logging.Filter)              # record.msg = record.getMessage() with secrets -> '***'; record.args = ()
```

### 7.14 `deploy/systemd/jev-gmail-labeler.service` (S2, exact content)

```ini
[Unit]
Description=Label new Gmail messages with Jev
Documentation=https://github.com/1npo/jev-gmail-labeler

[Service]
Type=simple
EnvironmentFile=%h/.config/jev-gmail-labeler/env
ExecStart=%h/.local/bin/jev-gmail-labeler listen
Restart=on-failure
RestartSec=10
RestartPreventExitStatus=2 3
TimeoutStopSec=60

[Install]
WantedBy=default.target
```
- This is a **user** unit. Install it at `~/.config/systemd/user/`, and run `loginctl enable-linger $USER` so it runs without a login.
- `env` holds `TYPESAFE_API_KEY=...` (chmod 600). Other settings live in `~/.config/jev-gmail-labeler/config.json`.
- Exit codes 2 and 3 need a human to fix something, so systemd doesn't restart on them.

---

## 8. Testing strategy

### 8.1 Tooling (in `pyproject.toml`)

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --import-mode=importlib --disable-socket --cov=jev_gmail_labeler --cov-report=term-missing"
[tool.coverage.run]
source = ["jev_gmail_labeler"]
branch = true
[tool.coverage.report]
fail_under = 95
show_missing = true
skip_covered = true
exclude_also = ["if TYPE_CHECKING:", "raise NotImplementedError", "if __name__ == .__main__.:"]
```
`--disable-socket` comes from `pytest-socket`, and blocks every network connection.

### 8.2 Disk guard: autouse fixture in `tests/conftest.py`

```python
class DiskIOBlocked(AssertionError): ...
@pytest.fixture(autouse=True)
def _block_disk_io(monkeypatch):
    roots = tuple({os.path.abspath(p) for p in (sys.prefix, sys.base_prefix, sysconfig.get_paths()['stdlib'],
                                               *site.getsitepackages())})
    real_open = builtins.open
    def guarded_open(file, mode='r', *args, **kwargs):
        if isinstance(file, int):
            return real_open(file, mode, *args, **kwargs)
        path = os.path.abspath(os.fspath(file))
        if not any(c in mode for c in 'wax+') and path.startswith(roots):   # read-only library data files
            return real_open(file, mode, *args, **kwargs)
        raise DiskIOBlocked(f'test tried to open {file!r} (mode {mode!r}); mock jev_gmail_labeler.files')
    def blocked(*a, **k): raise DiskIOBlocked(f'disk write blocked: {a!r}')
    real_connect = sqlite3.connect
    def guarded_connect(database, *a, **k):
        if database != ':memory:': raise DiskIOBlocked(f'sqlite file {database!r}')
        return real_connect(database, *a, **k)
    monkeypatch.setattr(builtins, 'open', guarded_open); monkeypatch.setattr(io, 'open', guarded_open)
    for name in ('replace', 'remove', 'unlink', 'mkdir', 'makedirs', 'rename', 'chmod'):
        monkeypatch.setattr(os, name, blocked)
    monkeypatch.setattr(sqlite3, 'connect', guarded_connect)
```
- `tests/test_guards.py` proves that `open('x','w')`, `os.replace`, `sqlite3.connect('f.db')` and `socket.create_connection(('example.com', 80))` all raise.
- `test_files.py` tests `files.py` without touching disk. For this to work, `files.py` uses `import os` and the bare builtin `open`. The tests then patch:
  - `monkeypatch.setattr(files, 'open', mock_open(read_data=...), raising=False)`
  - `monkeypatch.setattr(files, 'os', MagicMock())`
  - `monkeypatch.setattr(files, 'ensure_dir', MagicMock())` when testing `write_text_atomic`

  `ensure_dir` is tested with `MagicMock(spec=Path)`.
- Never use `tmp_path`.

### 8.3 Shared fixtures (`tests/conftest.py`)

| Fixture | Provides |
|---|---|
| `make_raw_message` | factory `(id='m1', subject='Hi', sender='A <a@x.com>', plain=None, html=None, label_ids=('INBOX',), attachment=False, snippet='') -> dict` building a Gmail API `format=full` message with base64url bodies and nested `multipart/alternative` |
| `email_message` | `EmailMessage` for `m1` |
| `anonymized_email` | `AnonymizedEmail` for `m1` |
| `criteria_data` / `criteria` | 3 categories (`receipt`, `news` with label `"Reading"`, `personal` with `label: null`), `min_confidence: 0.5`, `uncertain_label: "Unsure"` / parsed |
| `make_classification` | factory `(category='receipt', confidence=0.9) -> ClassificationResult` |
| `make_jev_response` | factory returning `SimpleNamespace(model='jev-1.13.0', request_id='req-1', usage=SimpleNamespace(input_tokens=1000, output_tokens=1), choices={'category': SimpleNamespace(choice=c, confidence=x, probabilities={...})})` |
| `typesafe_client` | `MagicMock()` whose `system_one.return_value` is a default jev response |
| `gmail_service` | `MagicMock()`; helper `api(service, 'messages.get')` returns the mock for `service.users().messages().get`, so tests set `.return_value.execute.return_value` |
| `make_http_error` | factory `(status, reason=None) -> googleapiclient.errors.HttpError(httplib2.Response({'status': status}), json-bytes)` |
| `gmail_client` | `MagicMock(spec=GmailClient)` |
| `label_manager` | `MagicMock(spec=LabelManager)` |
| `anonymizer` | `MagicMock(spec=Anonymizer)` returning `anonymized_email` |
| `make_pulled` | factory `(history_id, email='me@example.com', ack_id='a1') -> PulledMessage` with JSON bytes |
| `app_config` | `AppConfig` with paths under `Path('/fake')`, topic `projects/p/topics/t`, subscription `projects/p/subscriptions/s` |
| `memory_state` | `StateStore(':memory:', clock=fake_clock.time)` |
| `fake_clock` | object with `time()`, `monotonic()`, `sleep(s)` that advance an internal float |

**spaCy and Presidio** are never loaded. `test_anonymize.py` injects fake modules with `monkeypatch.setitem(sys.modules, 'presidio_analyzer', fake)` (and the same for `presidio_analyzer.nlp_engine`, `presidio_anonymizer` and `presidio_anonymizer.entities`). Because `Anonymizer` imports lazily, importing the package never pulls in spaCy.

### 8.4 Per-module test lists (minimum; add more to reach 95% line+branch)

| File | Tests |
|---|---|
| `test_models` | round-trip `to_dict`/`from_dict` for every model; enums serialize as values; `to_jev_state` keys exact |
| `test_errors` | default `transient` per class; override; `CriteriaError` str format |
| `test_files` | read_text ok and missing; read_json invalid → ValueError mentions path; write_text_atomic writes tmp, chmods when mode given, replaces; ensure_dir args |
| `test_config` | defaults; each precedence layer overrides the lower one (one test per adjacent pair); default config file absent is ok; unknown key; `typesafe_api_key` rejected; bad topic/subscription regex; range errors collected together; relative path resolution (file vs CLI); env `null`; `require` message; `resolve_typesafe_api_key` file vs env vs missing; `redacted_dict` never contains the key |
| `test_criteria` | example parses; derived label; `label: null`; prefix `""`; every validation rule has one failing test; multiple errors reported together; `choice_criteria` appends `_none` last and keeps order; `managed_label_names`; load missing/invalid file |
| `test_logging_setup` | journald format vs normal; redaction of a registered secret in msg and args; short secrets ignored; noisy loggers levels |
| `test_output` | JSON array shape; CSV header == `CSV_COLUMNS`, None → empty, probabilities JSON; write_results calls `files.write_text_atomic`; summary line counts and arrow |
| `test_quota` | under budget no sleep; over budget sleeps exact remaining window (fake clock); window expiry |
| `test_message_parser` | plain only; html only; both → html wins; nested multipart; attachment skipped (filename and attachmentId); invisible chars removed; double-escaped entity; snippet fallback; empty; encoded-word From/Subject; bad header fallback; charset from Content-Type; missing headers '' |
| `test_gmail_client` | each method's request args (userId, q, pageToken loop, labelFilterBehavior, historyTypes); pagination stops at max_results; list_history dedupes & uses last historyId; 404 → MessageNotFoundError / HistoryExpiredError; 401 → AuthError; 429/500 transient; 400 non-transient; RefreshError → AuthError; OSError transient; quota consumed with COSTS; `num_retries` passed |
| `test_auth` | load ok; missing → AuthError; missing scopes; expired + refresh saves with 0o600; RefreshError → AuthError; consent flow uses from_client_config + run_local_server(port, open_browser) and saves; missing credentials → ConfigError; secrets registered |
| `test_labels` | find case-insensitive; ensure existing; ensure creates ancestors in order; 409 reloads; existing_ids never creates; cache loaded once |
| `test_classifier` | questions built from criteria; state passed; result mapping incl. cost & elapsed; unknown choice; each exception mapping row; client constructed with RetryPolicy(max_retries=4, timeout=60.0) when not injected (patch `TypeSafeClient`); close/context manager |
| `test_anonymize` | lazy (no import at init); replace operator used; truncation before analysis + flag; empty text; missing model → ConfigError; engine cached; other error → AnonymizationError |
| `test_pipeline` | decide: matched / no_match / low_confidence / null label; process: not_found, already_labelled, force bypass, dry run (would_label, no ensure), apply (ensure + modify), force+apply removes other managed labels, no_label, error in each step with retryable flag, AuthError propagates; process_many yields in order |
| `test_state` (S2) | schema; kv get/set/delete; advance_cursor monotonic; mark_* and message(); due_retries ordering; prune; clear_watch; file path calls files.ensure_dir (patch sqlite3.connect in module) |
| `test_pubsub` (S2) | parse valid/str historyId/invalid JSON/missing keys; pull maps responses; DeadlineExceeded → []; Unauthenticated/NotFound/PermissionDenied/Unavailable mapping; ack/nack request shapes; empty ack no call |
| `test_service` (S2) | renew_watch: force, fresh, not due, due by age, due by expiry, seeds cursor; handle_batch: stale/duplicate → ack without sync, new → sync + ack, foreign email ignored, invalid data acked, transient Gmail error → nack; sync: seeds cursor, processes ids then advances, HistoryExpired → catch_up; catch_up query/order/baseline; process: done skip, retry not due skip, error → retry backoff values, max attempts → failed, non-retryable → failed, dry_run no state; run: loop exits on stop event, idle resync, pubsub backoff, signals installed, resources closed, AuthError propagates |
| `test_app` (S2) | factories wire objects (patch constructors); listen requires topic/subscription |
| `test_cli` (S2) | every command and subcommand happy path (patch app factories); label selector rules & mutual exclusion; dry-run vs --apply; output json stdout / json file / csv by extension / --format override; summary on stderr; exit codes 0/1/2/3/4/130; config show redacted; criteria validate/example; watch start/stop/status; `--version` |
| `test_main` (S2) | `runpy.run_module('jev_gmail_labeler', run_name='__main__')` with `cli.main` patched → SystemExit code |

---

## 9. `pyproject.toml` (S1)

- `[project]`:
  - `name = "jev-gmail-labeler"`, `dynamic = ["version"]`.
  - `description = "Label Gmail messages in real time with Jev"`.
  - `requires-python = ">=3.13"`.
  - `[project.scripts] jev-gmail-labeler = "jev_gmail_labeler.cli:main"`.
- Runtime deps. Use `uv add` / `uv remove`, so uv picks current lower bounds.
  - Keep `beautifulsoup4`, `lxml`, `google-api-python-client`, `google-auth`, `google-auth-oauthlib`, `presidio-analyzer`, `presidio-anonymizer` and `spacy`.
  - Raise the floor to `typesafe-sdk>=0.7.2`.
  - Add `google-cloud-pubsub`, `platformdirs` and `"en-core-web-md @ https://github.com/explosion/spacy-models/releases/download/en_core_web_md-3.8.0/en_core_web_md-3.8.0-py3-none-any.whl"`.
  - Remove `pandas` and `ipython`.
- Dev group: `pytest`, `pytest-cov`, `pytest-socket`, `ruff`. Remove `ipython`.
- Build settings:
  - `[tool.hatch.version] path = "src/jev_gmail_labeler/_version.py"`
  - `[tool.hatch.build.targets.wheel] packages = ["src/jev_gmail_labeler"]`
  - `[tool.hatch.metadata] allow-direct-references = true`
  - `[tool.uv] package = true`
- Ruff: keep the existing `[tool.ruff]`, `lint.select` and `format` settings. Add `src = ["src", "tests"]`, and remove the `conftest.py F403` ignore.
- Append these lines to `.gitignore`: `.coverage`, `htmlcov/`, `.pytest_cache/`, `.ruff_cache/`, `*.sqlite3`, `.env`.

---

## 10. Documentation plan (S3, written last)

`docs/` holds usage and setup docs only:
- `docs/user-guide.md` (new)
- `docs/ABOUT_JEV.md` (moved)

`planning/` holds only `PLAN.md` and `prompts/`. The README must not link into `planning/`.

**User guide outline** (plain language; define each technical term once, on first use):
1. **What it does.** The 4-step pipeline. What leaves your machine: the anonymized body plus headers go to TypeSafe; Google sees only label changes.
2. **What you need.** Linux with systemd (for the service), Python 3.13, uv, a Google account, a TypeSafe API key, and optionally the gcloud CLI.
3. **Install.** Run `uv tool install git+https://github.com/1npo/jev-gmail-labeler.git` (about 33 MB of it is the spaCy language model). Add an optional subsection, "Use the larger language model". It gives the `uv tool install --with "en-core-web-lg @ https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl" ...` command and says to set `spacy_model` to `en_core_web_lg`. The trade-off: about 400 MB on disk, slower startup, and better name detection.
4. **Set up Google Cloud.** Give Console steps and the equivalent `gcloud` commands:
   - Create a project and enable `gmail.googleapis.com` and `pubsub.googleapis.com`. A billing account may be required for Pub/Sub; this volume fits in the free tier.
   - In Google Auth Platform: Branding; Audience (External; add yourself as a test user); Data Access (add the `gmail.modify` and `pubsub` scopes).
   - Create a Desktop app OAuth client and save it as `~/.config/jev-gmail-labeler/credentials.json`.
   - Create the topic: `gcloud pubsub topics create gmail-labeler`.
   - Grant Gmail publish rights: `gcloud pubsub topics add-iam-policy-binding gmail-labeler --member=serviceAccount:gmail-api-push@system.gserviceaccount.com --role=roles/pubsub.publisher`.
   - Create the subscription: `gcloud pubsub subscriptions create gmail-labeler-sub --topic=gmail-labeler --ack-deadline=300 --expiration-period=never`.
   - **Publish the app.** Google Auth Platform → Audience → Publish app. Explain why: in Testing, the token expires every 7 days. Explain what the user will see: the one-time "Google hasn't verified this app" screen, and how to continue past it (Advanced → Go to *app name*).
   - Each of these must be its own clearly titled step with both Console and `gcloud` instructions:
     - granting the topic Publisher role to `gmail-api-push@system.gserviceaccount.com`
     - creating the subscription with `--expiration-period=never` (and why: by default, unused subscriptions are deleted after 31 days)
     - adding the `pubsub` scope
5. **Configure.** Cover:
   - `config.json` with a full example.
   - The `env` file with the API key (`chmod 600`).
   - The precedence list (D12).
   - The full config reference table (§5.1).
   - `config show`.
6. **Authorize Gmail.** Run `jev-gmail-labeler auth`. If you authorized before publishing the app or adding the `pubsub` scope, delete the old token and run `auth` again. On a headless server: `ssh -L 8765:localhost:8765 server`, then `jev-gmail-labeler auth --no-browser --port 8765` and open the printed URL on your laptop.
7. **Write your email criteria.** The schema table (§4.1), the example (§4.2), and how labels are named and created. Explain what happens on no match or low confidence, and how to use `criteria validate` and `criteria example`. Tips from the Jev docs:
   - Write descriptions that tell options apart; use `what`/`not_for` objects when categories get confused.
   - Option order can bias answers, so test reorderings.
   - Restart the service after editing.
8. **Run it manually.** `label` examples (`--count 20`, `--query "from:shop.example newer_than:7d"`, `--id`), dry run vs `--apply`, `--force`, output formats with a sample JSON result and the CSV columns, exit codes, and cost per email (≈ input tokens × $0.042/M).
9. **Run it as a service.** Copy the unit from `deploy/systemd/`, then:
   - `systemctl --user daemon-reload && systemctl --user enable --now jev-gmail-labeler`
   - `loginctl enable-linger $USER`
   - `journalctl --user -u jev-gmail-labeler -f`
   - Explain what the service does: watch renewal, catch-up, retries, and how state is kept at `~/.local/state/...`.
   - Stop it, upgrade with `uv tool upgrade jev-gmail-labeler`, and use `watch stop`.
10. **Maintenance.** Re-authorizing, rotating the API key, resetting state (delete the state DB, then run `watch start`), and pinning `jev_model`.
11. **Troubleshooting** table covering: no notifications (the IAM grant is on the topic; topic name), `invalid_grant` weekly (Testing mode), exit 3 in the logs, subscription deleted (the expiration period), a missing spaCy model, and 429s.
12. **Uninstall.** `watch stop`, disable the unit, `uv tool uninstall`, delete the config and state dirs, and revoke access at myaccount.google.com/permissions.

**README outline:**
1. Title and one-line description.
2. What it does: 4 bullets.
3. How it works: a short paragraph and a text diagram, Gmail → Pub/Sub → listener → anonymize → Jev → label.
4. Requirements.
5. Install.
6. Quick start: 6 numbered steps, each linking to a guide section.
7. Usage: 4 short examples.
8. Documentation: links to `docs/user-guide.md` and `docs/ABOUT_JEV.md`.
9. Privacy.
10. Development: `uv sync`, `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`.
11. LLM use disclosure (updated: planned by Claude Opus, implemented by Claude Sonnet sessions).

---

## S1. Session 1: foundation, Google and TypeSafe subpackages, pipeline

- **Goal:** the library layer, complete and tested: models, config, criteria, files, logging, output, `google_api` (auth, quota, parser, gmail, labels), `typesafe_api`, anonymizer and pipeline. `cli.py` is a stub.
- **Branch:** `rewrite/core`

| # | Task | Commit message |
|---|---|---|
| 1 | Preflight (§0). Save the PoC `EMAIL_CRITERIA` text for task 5 (from `git show d799b74:jev_gmail_labeler/main.py`). | — |
| 2 | Delete `jev_gmail_labeler/`, `main.py` and `_version.py`. Create the `src/` tree (§2.1, S1 files only, with empty modules where needed). Write `_version.py` (`0.2.0`) and `__init__.py`, and a stub `cli.py` with `main(argv=None) -> int` that parses only `--version` and returns 0. Rewrite `pyproject.toml` (§9), update `.gitignore`, and run `uv lock` and `uv sync`. Add `tests/conftest.py` with the guards (§8.2), plus `test_guards.py`. | `Restructure into src layout with test guards` |
| 3 | `errors.py`, `models.py`, `files.py` and their tests | `Add error types, data models and file helpers` |
| 4 | `config.py` and tests | `Add layered configuration` |
| 5 | `criteria.py` (incl. `EXAMPLE_CRITERIA`) and tests | `Add email criteria schema and validation` |
| 6 | `logging_setup.py`, `output.py` and tests | `Add logging setup and result output` |
| 7 | `google_api/quota.py`, `message_parser.py` and tests | `Add Gmail message parser and quota limiter` |
| 8 | `google_api/gmail.py`, `auth.py` and tests | `Add Gmail client and OAuth helpers` |
| 9 | `google_api/labels.py` and tests | `Add Gmail label manager` |
| 10 | `typesafe_api/classifier.py` and tests | `Add Jev classifier` |
| 11 | `anonymize.py` and tests | `Add Presidio anonymizer` |
| 12 | `pipeline.py` and tests, plus the shared fixtures from §8.3 that it needs | `Add labelling pipeline` |
| 13 | Verify, then merge and push (§0.6) | merge commit |

Add each conftest fixture in the commit that first uses it.

**Done when:**
- [ ] `uv sync` succeeds
- [ ] `uv run ruff check .` and `uv run ruff format --check .` are clean
- [ ] `uv run pytest` passes with total coverage ≥95% (enforced by `fail_under`)
- [ ] `uv run python -c "import sys, jev_gmail_labeler.pipeline; assert 'spacy' not in sys.modules"` succeeds
- [ ] `git log --oneline main..rewrite/core` shows the atomic commits; `rewrite/core` is merged into `main`; push attempted

## S2. Session 2: state, Pub/Sub, service, CLI, systemd

- **Goal:** a working `jev-gmail-labeler` with all commands (§6), the listener service (§7.9–7.12), the systemd unit, and `sandbox/` removed.
- **Branch:** `rewrite/service`. S1 must already be merged into `main`; don't change S1 public interfaces. If an interface is genuinely wrong, fix it in a separate commit and say so in the report.

| # | Task | Commit message |
|---|---|---|
| 1 | Preflight. `uv sync`, then `uv run pytest` must pass before you start. | — |
| 2 | `state.py` and tests | `Add SQLite state store` |
| 3 | `google_api/pubsub.py` and tests | `Add Pub/Sub puller` |
| 4 | `service.py` (`renew_watch`, `LabelerService`) and tests | `Add listener service` |
| 5 | `app.py` and tests | `Add application factories` |
| 6 | Full `cli.py` (§6), `__main__.py` and tests | `Add command-line interface` |
| 7 | `deploy/systemd/jev-gmail-labeler.service` (§7.14 verbatim) | `Add systemd user unit` |
| 8 | `git rm -r sandbox` | `Remove sandbox listener prototype` |
| 9 | Verify, then merge and push | merge commit |

**Done when:**
- [ ] ruff check and format are clean; `uv run pytest` passes with coverage ≥95%
- [ ] `uv run jev-gmail-labeler --help` lists `auth`, `label`, `listen`, `watch`, `criteria` and `config`, and each `<cmd> --help` works
- [ ] `uv run jev-gmail-labeler criteria example` emits JSON that the command's own validation accepts. Check this with the test that parses `EXAMPLE_CRITERIA`; do not write a temp file.
- [ ] `uv run jev-gmail-labeler label --id x --count 2` exits 2
- [ ] `sandbox/` is gone; merged into `main`; push attempted
- [ ] Do **not** run `auth`, `label`, `listen` or `watch` against real accounts. That is the user's job.

## S3. Session 3: documentation and file moves

- **Goal:** the docs in §10, the moves, and a final consistency check. No code changes, except fixing a doc/CLI mismatch you find (in a separate commit).
- **Branch:** `docs/user-guide`

| # | Task | Commit message |
|---|---|---|
| 1 | Preflight; `uv sync`; `uv run pytest` passes | — |
| 2 | `git mv ABOUT_JEV.md docs/ABOUT_JEV.md`; `git mv prompts planning/prompts` | `Move Jev notes to docs and prompts to planning` |
| 3 | Write `docs/user-guide.md` (§10 outline). Copy every command and flag from actual `--help` output and every config key from `config.py`. | `Add user guide` |
| 4 | Rewrite `README.md` (§10 outline) | `Rewrite README for the new tool` |
| 5 | Re-read both docs for plain language and accuracy against the code. Fix any mismatches. | `Polish documentation` (only if changes) |
| 6 | Verify, then merge and push | merge commit |

**Done when:**
- [ ] Every relative link in `README.md` and `docs/*.md` points to an existing file. Check with `grep -o '](\(docs\|\.\./\|\./\)[^)#]*' README.md docs/*.md` and confirm each path exists.
- [ ] README has no link into `planning/`
- [ ] Repo root contains only: `README.md`, `pyproject.toml`, `uv.lock`, `.gitignore`, `.python-version`, `src/`, `tests/`, `docs/`, `planning/`, `deploy/` (plus `.git/` and git-ignored items such as `workspace/`, `token.json`, `.venv/` and caches)
- [ ] ruff and pytest (≥95%) still pass; merged into `main`; push attempted

---

## 11. Risks and open questions for the user (do these yourself)

1. **Before S1:**
   - Allow git on this share: `git config --global --add safe.directory '%(prefix)///diatom/nick/dev/sandbox/jev-gmail-labeler'`.
   - Set a git identity on the machine that runs the sessions (done).
   - Make the tree clean. Commit or discard the deletion of `prompts/create-jev-labeler.md` and the untracked `prompts/plan-redesign-prompt.md`, because S3 moves `prompts/`.

   *Status: done (user, 2026-10-03). All sessions run on `diatom` with the `claude` CLI.*
2. **Push:** `origin` uses the SSH alias `github-1npo`, which must resolve on `diatom`.
3. **Google Cloud (user, after all sessions).** Follow the steps in the user guide (§10 item 4), which must give explicit Console and `gcloud` instructions for each:
   - Enable the APIs.
   - Add the `pubsub` scope to the consent screen.
   - Create the topic and **grant `gmail-api-push@system.gserviceaccount.com` Publisher on the topic**.
   - Create the subscription with `--expiration-period=never`; by default, subscriptions are deleted after 31 days of inactivity.
4. **OAuth publishing status: decided.** The user will publish the app ("In production") and accept the one-time unverified-app warning, then re-run `auth`. Existing `token.json` files lack the new `pubsub` scope, so re-running `auth` is required anyway. The user guide must document both steps.
5. **Behaviour changes: accepted by the user (2026-10-03):**
   - The `from_government` question and the `report` command are dropped (D8, D11).
   - Presidio switches from *redact* to *replace* (D9).
   - Only INBOX mail is processed by the service (D5).
   - The default spaCy model is `en_core_web_md` (about 33 MB). `en_core_web_lg` is an optional install (D10).
6. **Jev model alias:** `jev-latest` can change under you. Once you have tuned `min_confidence`, set `jev_model` to `jev-1.13.0`.

---

## 12. Requirements → task checklist

| Requirement | Where |
|---|---|
| Work in this project; preserve `workspace/` and keep it ignored; token and credentials ignored | §0.1, §9 (.gitignore append only), S1-2 |
| Atomic commits; branch per unit; merge + push when complete | §0.6, S1/S2/S3 tables |
| `src/` layout; Google subpackage; TypeSafe subpackage | §2.1, D1, S1-2, S1-7..10 |
| Remove `sandbox` after rewrite | S2-8 |
| `ABOUT_JEV.md` → `docs/`; `prompts` → `planning/prompts`; `docs/` usage-only; README doesn't link `planning/` | S3-2, §10, S3 done-when |
| argparse primary UI; adjusted commands | §6, S2-6 |
| Config via JSON string or file | §5, S1-4, `--config` / `--config-json` |
| Criteria from a JSON file | §4, S1-5 |
| ≥95% coverage | §8.1 `fail_under`, every session's done-when |
| Mock all disk and network I/O; guard | §8.2, S1-2 |
| Fixtures as pytest fixtures | §8.3 |
| Pipeline steps accept and return dataclasses, no JSON files | §3.2, §7.8 |
| New labelling step | §4.4, §7.6, §7.8 step 6 |
| Pub/Sub pull listener, run pipeline per email | §7.10, §7.11, S2-3/4 |
| systemd service | §7.14, S2-7, guide §9 |
| Manual: last N / query; JSON stdout or CSV/JSON file; apply or not | §6 `label`, S2-6 |
| Watch creation and renewal; historyId state; duplicates; ack/nack; retry/backoff; shutdown; logging; idempotency | §7.9–7.11, §7.13, D4, D7 |
| Token refresh; rate limits; per-email failure isolation; large or HTML-only bodies | §7.1, D18/§7.2, §7.8 errors, §7.11 process, §7.4, §7.5 truncation |
| Secrets never logged | §5.3, §7.13 |
| User guide: GCP setup, systemd, CLI, criteria; README entry point; plain language; docs last | §10, S3 |
| TypeSafe API matches current SDK (0.7.x: `TypeSafeClient.system_one`, `Choice`, `RetryPolicy`, `choices[...]`, `usage`, `request_id`) | §7.7 |
| Risks and open questions | §11 |

---

## 13. Kickoff prompts

### Session 1

```
You are implementing Session 1 of a planned rewrite of this repository (run from the repo root).
Read planning/PLAN.md in full first, then execute section "S1" exactly, task by task, committing at
each listed boundary. All design decisions are in the plan; do not redesign. Follow §0 ground rules
strictly: never open anything in workspace/, token.json, credentials.json; don't delete or recreate .venv;
tests must never touch disk or network. When every "Done when"
check passes, merge rewrite/core into main and push (report the command if push fails). Finish with a
short report: commits made, coverage %, any deviation from the plan and why.
```

### Session 2

```
You are implementing Session 2 of a planned rewrite of this repository (run from the repo root).
Session 1 is already merged into main. Read planning/PLAN.md in full first, then execute section "S2"
exactly, task by task, committing at each listed boundary. Reuse the S1 modules and fixtures as they
exist in src/ and tests/; do not redesign. Follow §0 ground rules strictly (no workspace/ access,
don't delete .venv, no disk/network in tests, never run auth/label/listen/watch against real
accounts). When every "Done when" check passes, merge rewrite/service into main and push (report the
command if push fails). Finish with a short report: commits, coverage %, deviations and why.
```

### Session 3

```
You are implementing Session 3 (documentation) of a planned rewrite of this repository (run from the repo root).
Sessions 1 and 2 are merged into main. Read planning/PLAN.md in full first, then execute section "S3"
exactly. Write docs from the real code: copy commands/flags from `uv run jev-gmail-labeler <cmd> --help`
and config keys from src/jev_gmail_labeler/config.py. Plain, concise language; jargon only when
necessary and explained once. docs/ is usage/setup only; the README must not link into planning/.
Follow §0 ground rules (never open workspace/). When every "Done when" check passes, merge
docs/user-guide into main and push (report the command if push fails). Finish with a short report.
```
