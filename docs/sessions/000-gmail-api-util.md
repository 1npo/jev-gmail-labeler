# Session Log: Gmail API Setup & `gmail_api_util.py`

**Date:** 2026-10-03
**Project:** jev-gmail-labeler

## Summary

This session covered two things: setting up Gmail API access for a personal
Gmail account via Google Cloud Console, and iteratively building a Python
module, `gmail_api_util.py`, for fetching Gmail messages (with JSON
caching) and managing labels.

## 1. Gmail API setup (Google Cloud Console)

- Created a Cloud project and enabled the Gmail API.
- Gmail API requires **OAuth 2.0**, not a simple API key, since it accesses
  private mailbox data.
- OAuth consent screen configured as **External** user type; added self as
  a test user (required while the app is unverified).
- **Note:** Google reorganized this UI — test users now live under the
  **Audience** tab on the "Google Auth Platform" pages, not a separate
  "OAuth consent screen" page as older guides describe.
- Created an OAuth Client ID (Desktop app type) and downloaded
  `credentials.json`.

## 2. Gmail API quota research

- `messages.get` costs 20 quota units/call; `messages.list` costs 5
  units/call (same cost regardless of `format=` used).
- The practical bottleneck for bulk fetching is the **per-user-per-minute
  limit of 6,000 units** (not the 80,000,000/day billing threshold, which
  is far higher and doesn't block usage — it's currently free regardless).
- Example: fetching sender info for 38,000 messages ≈ 760,000 units ≈ a
  floor of ~2+ hours given the per-minute cap.

## 3. `gmail_api_util.py` — final public API

(Originally named `gmail_fetcher.py`; renamed to `gmail_api_util.py`
mid-session.)

| Function | Purpose |
|---|---|
| `get_credentials()`, `build_gmail_service()` | OAuth flow + authenticated service object |
| `fetch_emails(max_results, query, label_ids, cache_path="email_cache.json", ...)` → `dict[str, EmailMessage]` | Fetches full messages (subject, body, metadata — attachments always excluded). **Caches to JSON by default**, skips already-cached IDs, safe to interrupt and resume. |
| `load_cached_emails(cache_path)` → `dict[str, EmailMessage]` | Reads a cache file with zero API calls. |
| `fetch_senders(max_results, query, label_ids, cache_path="sender_cache.json", ...)` → `dict[str, SenderInfo]` | Cheap sender name + email lookups (`format="metadata"`); same caching/resume pattern as `fetch_emails`. |
| `list_labels()`, `get_label_map()` | List/lookup labels (system + custom). |
| `create_label()`, `get_or_create_label()`, `update_label()`, `delete_label()` | Label CRUD. |
| `apply_labels(message_id, add, remove)` | Add/remove labels on one message. |
| `batch_apply_labels(message_ids, add, remove)` | Same, up to 1,000 messages per call (50 quota units total vs. 5/message individually). |
| `QuotaRateLimiter` | Paces calls to stay under the 6,000 units/min per-user quota. |
| `_execute_with_backoff()` | Retries 403/429/500/503 errors with exponential backoff. |

### Key design decisions

- **OAuth scope: `gmail.modify`.** Needed because applying labels to
  messages (`messages.modify` / `batchModify`) requires this broader
  scope — `gmail.labels` alone only covers label CRUD, not applying them.
  **Important:** any `token.json` saved under the earlier, narrower
  `gmail.readonly` scope must be deleted to force re-consent under the new
  scope.
- **Caching:** JSON files keyed by message ID, written atomically
  (temp file + `os.replace`) so an interrupted write can't corrupt the
  cache file. `fetch_emails` and `fetch_senders` both cache by default.
- **API evolution:** `fetch_emails` started as a plain, non-caching
  `list[EmailMessage]`-returning function. A separate `fetch_emails_cached`
  was added for JSON caching, then the two were **consolidated back into a
  single `fetch_emails()`** that caches by default and returns
  `dict[str, EmailMessage]` (keyed by message ID) — mirroring
  `fetch_senders`'s shape. Any code written against the earlier
  list-returning version needs updating (iterate with `.values()`).

## Files produced this session

- `gmail_api_util.py` — the module described above.
