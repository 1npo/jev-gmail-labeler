"""
gmail_api_util.py

A small module for authenticating with the Gmail API: fetching email
messages as plain Python objects (attachments are skipped entirely — only
message metadata and text/plain / text/html bodies are returned), and
managing labels (creating them and applying/removing them on messages).

Setup
-----
1. pip install google-auth google-auth-oauthlib google-api-python-client
2. Place the OAuth "credentials.json" you downloaded from Google Cloud
   Console (Desktop app client) in the same directory as this module,
   or pass a custom path to `fetch_emails(credentials_path=...)`.
3. The first time you run this, a browser window will open for you to
   log in and grant access. A `token.json` file is then saved so you
   won't have to log in again until the token is revoked or expires.

   NOTE: this module uses the gmail.modify scope (needed for label
   management), which is broader than the gmail.readonly scope an
   earlier version of this module used. If you have an existing
   token.json saved from that version, delete it before running
   anything here so you're re-prompted to consent with the new scope
   — otherwise calls will fail with an insufficient-scope error.

Example
-------
    from gmail_api_util import fetch_emails, fetch_senders, list_labels, apply_labels

    # Fetch full messages (subject, body, metadata -- no attachments),
    # cached to JSON by default so an interrupted run can resume:
    emails = fetch_emails(max_results=5000, query="is:unread")
    for msg in emails.values():
        print(msg.subject, "-", msg.sender)
        print(msg.body_text[:200])

    # Later, or in another process, load that cache with no API calls:
    emails = load_cached_emails("email_cache.json")

    # Fetch just sender name/email for a large batch, with the same
    # on-disk caching / resume-on-interrupt behavior:
    senders = fetch_senders(max_results=38000, cache_path="sender_cache.json")
    for mid, info in senders.items():
        print(info.name, "<" + info.email + ">")

    # Labels: create one, then apply it to a message.
    label = get_or_create_label("Receipts/2026")
    apply_labels(message_id="...", add=[label.id])
"""

from __future__ import annotations

import base64
import json
import os
import random
import time
from dataclasses import asdict, dataclass, field
from email.header import decode_header
from email.utils import parseaddr

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# gmail.modify covers everything this module does: reading messages,
# managing labels, and applying/removing labels on messages. (gmail.labels
# alone would cover label create/read/update/delete but NOT applying a
# label to a message -- that call, messages.modify/batchModify, requires
# gmail.modify.)
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


@dataclass
class EmailMessage:
    """
    A full representation of a Gmail message: subject, body, and metadata,
    with attachments always excluded.
    """

    id: str
    thread_id: str
    subject: str
    sender: str
    to: str
    cc: str
    bcc: str
    reply_to: str
    date: str
    message_id: str  # the Message-ID header (globally unique; not Gmail's `id`)
    snippet: str
    body_text: str
    body_html: str | None = None
    labels: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)  # every header, raw

    def __repr__(self) -> str:
        return f"EmailMessage(id={self.id!r}, subject={self.subject!r}, sender={self.sender!r})"


@dataclass
class SenderInfo:
    """The sender's display name and email address for one message."""

    message_id: str
    name: str
    email: str
    raw: str  # the original, unparsed "From" header

    def __repr__(self) -> str:
        return f"SenderInfo(name={self.name!r}, email={self.email!r})"


@dataclass
class Label:
    """A Gmail label — either a built-in system label (INBOX, SENT, ...)
    or a user-created one."""

    id: str
    name: str
    type: str  # "system" or "user"
    message_list_visibility: str | None = None
    label_list_visibility: str | None = None
    text_color: str | None = None
    background_color: str | None = None

    def __repr__(self) -> str:
        return f"Label(id={self.id!r}, name={self.name!r}, type={self.type!r})"


def get_credentials(
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
    scopes: list[str] = SCOPES,
) -> Credentials:
    """
    Load cached OAuth credentials, refreshing or requesting new ones as needed.

    On first run this opens a browser window for the consent flow and
    writes the resulting token to `token_path`. On later runs it reuses
    (and silently refreshes) that saved token.
    """
    creds: Credentials | None = None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(
                    f"Couldn't find '{credentials_path}'. Download it from Google Cloud "
                    "Console (APIs & Services > Credentials > your OAuth client) and "
                    "place it next to this script, or pass credentials_path=..."
                )
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, scopes)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w") as token_file:
            token_file.write(creds.to_json())

    return creds


def build_gmail_service(creds: Credentials):
    """Build and return an authenticated Gmail API service object."""
    return build("gmail", "v1", credentials=creds)


class QuotaRateLimiter:
    """
    Paces requests to stay under the Gmail API's per-user-per-minute quota
    (6,000 units as of the API's current published limits). Call
    consume(units) right before executing each request; it sleeps as needed
    to avoid exceeding the budget for the current 60-second window.
    """

    def __init__(self, units_per_minute: int = 6000, safety_margin: float = 0.85):
        # safety_margin leaves headroom for retries and other concurrent use.
        self.budget = units_per_minute * safety_margin
        self.window_start = time.monotonic()
        self.used = 0

    def consume(self, units: int) -> None:
        now = time.monotonic()
        elapsed = now - self.window_start
        if elapsed >= 60:
            self.window_start = now
            self.used = 0
            elapsed = 0
        if self.used + units > self.budget:
            time.sleep(max(0.0, 60 - elapsed))
            self.window_start = time.monotonic()
            self.used = 0
        self.used += units


def _execute_with_backoff(request, max_retries: int = 6):
    """
    Execute a Gmail API request, retrying with exponential backoff on
    rate-limit (403/429) or transient server (500/503) errors, per Google's
    recommended retry strategy.
    """
    for attempt in range(max_retries):
        try:
            return request.execute()
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            if status in (403, 429, 500, 503) and attempt < max_retries - 1:
                time.sleep((2**attempt) + random.random())
                continue
            raise


def _decode_body(data: str) -> str:
    """Decode Gmail's URL-safe base64 body data into text."""
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _extract_bodies(payload: dict) -> tuple[str, str | None]:
    """
    Walk a message payload's MIME parts and pull out text/plain and
    text/html bodies, skipping anything that looks like an attachment
    (i.e. any part with a filename, or non-text content).
    """
    text_parts: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict) -> None:
        mime_type = part.get("mimeType", "")
        filename = part.get("filename", "")
        body = part.get("body", {})

        # Skip attachments: they carry a filename and/or an attachmentId
        # instead of inline data.
        is_attachment = bool(filename) or "attachmentId" in body

        if not is_attachment:
            data = body.get("data")
            if data:
                if mime_type == "text/plain":
                    text_parts.append(_decode_body(data))
                elif mime_type == "text/html":
                    html_parts.append(_decode_body(data))

        for sub_part in part.get("parts", []) or []:
            walk(sub_part)

    walk(payload)

    body_text = "\n".join(text_parts).strip()
    body_html = "\n".join(html_parts).strip() if html_parts else None
    return body_text, body_html


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode_header_value(value: str) -> str:
    """
    Decode a MIME encoded-word header value (e.g. '=?UTF-8?B?Sm9zw6k=?=')
    into plain text. Many non-ASCII sender display names arrive encoded
    this way; plain-ASCII names pass through unchanged.
    """
    if not value:
        return value
    decoded = ""
    for text, charset in decode_header(value):
        if isinstance(text, bytes):
            decoded += text.decode(charset or "utf-8", errors="replace")
        else:
            decoded += text
    return decoded


def _load_json_cache(cache_path: str) -> dict:
    """Load a JSON cache file from disk, tolerating a missing or corrupt file."""
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                # A previous run may have been killed mid-write; start fresh
                # rather than crashing on a corrupt cache file.
                return {}
    return {}


def _save_json_cache(cache_path: str, data: dict) -> None:
    """
    Write a JSON cache file to disk atomically: write to a temp file, then
    rename over the real path. This means an interruption during the write
    itself can never leave a half-written, corrupt cache file behind.
    """
    tmp_path = cache_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, cache_path)


def _parse_message(raw: dict) -> EmailMessage:
    payload = raw.get("payload", {})
    header_list = payload.get("headers", [])
    body_text, body_html = _extract_bodies(payload)

    # Keep every header (Return-Path, Received, List-Unsubscribe, custom
    # X- headers, etc.) available for callers who need something beyond
    # the commonly-used fields pulled out below.
    all_headers = {h.get("name", ""): h.get("value", "") for h in header_list}

    return EmailMessage(
        id=raw.get("id", ""),
        thread_id=raw.get("threadId", ""),
        subject=_header(header_list, "Subject"),
        sender=_header(header_list, "From"),
        to=_header(header_list, "To"),
        cc=_header(header_list, "Cc"),
        bcc=_header(header_list, "Bcc"),
        reply_to=_header(header_list, "Reply-To"),
        date=_header(header_list, "Date"),
        message_id=_header(header_list, "Message-ID"),
        snippet=raw.get("snippet", ""),
        body_text=body_text,
        body_html=body_html,
        labels=raw.get("labelIds", []) or [],
        headers=all_headers,
    )


def fetch_emails(
    max_results: int = 1000,
    query: str | None = None,
    label_ids: list[str] | None = None,
    cache_path: str = "email_cache.json",
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
    save_every: int = 25,
) -> dict[str, EmailMessage]:
    """
    Fetch messages from Gmail and return them as EmailMessage objects
    (subject, body, and metadata -- attachments are never included).

    Caches each message to a local JSON file (`cache_path`), keyed by
    message ID, and skips re-fetching any ID already in the cache -- same
    resumable-on-interrupt behavior as fetch_senders(). Interrupt this at
    any point (Ctrl+C) and re-run the same call later to resume without
    re-spending quota on messages already saved. Progress is flushed to
    disk atomically every `save_every` new messages, and again on
    exit/interrupt/error.

    Parameters
    ----------
    max_results : maximum number of messages to consider (across the whole
                  cache, not just this run).
    query       : optional Gmail search query, e.g. "is:unread", "from:someone@example.com",
                  "after:2026/09/01". Same syntax as the Gmail search bar.
    label_ids   : optional list of label IDs to filter by, e.g. ["INBOX"].
    cache_path  : JSON file used to persist {message_id: EmailMessage-as-dict}.
                  Reused and extended on subsequent calls.
    save_every  : flush the cache to disk after this many *new* messages
                  fetched (in addition to always saving on exit/interrupt/error).
    credentials_path / token_path : see get_credentials().

    Returns
    -------
    dict[str, EmailMessage] mapping message ID -> EmailMessage, covering
    every ID in the requested range (served fresh or from cache).
    """
    cache = _load_json_cache(cache_path)
    creds = get_credentials(credentials_path, token_path)
    service = build_gmail_service(creds)
    limiter = QuotaRateLimiter()

    # 1. Collect the message IDs in scope (messages.list costs 5 units/call).
    message_ids: list[str] = []
    page_token: str | None = None
    while len(message_ids) < max_results:
        remaining = max_results - len(message_ids)
        list_kwargs = {"userId": "me", "maxResults": min(remaining, 500)}
        if query:
            list_kwargs["q"] = query
        if label_ids:
            list_kwargs["labelIds"] = label_ids
        if page_token:
            list_kwargs["pageToken"] = page_token

        limiter.consume(5)
        response = _execute_with_backoff(service.users().messages().list(**list_kwargs))
        refs = response.get("messages", [])
        if not refs:
            break
        message_ids.extend(ref["id"] for ref in refs)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    # 2. Fetch (format="full", 20 units/call) each message not already cached.
    to_fetch = [mid for mid in message_ids if mid not in cache]
    print(
        f"{len(message_ids)} messages in range, {len(cache)} already cached, "
        f"{len(to_fetch)} left to fetch."
    )

    fetched_since_save = 0
    try:
        for i, mid in enumerate(to_fetch, start=1):
            limiter.consume(20)
            full_message = _execute_with_backoff(
                service.users().messages().get(userId="me", id=mid, format="full")
            )
            email_msg = _parse_message(full_message)
            cache[mid] = asdict(email_msg)
            fetched_since_save += 1

            if fetched_since_save >= save_every:
                _save_json_cache(cache_path, cache)
                fetched_since_save = 0
                print(f"  ...{i}/{len(to_fetch)} fetched, cache saved.")
    finally:
        # Always persist whatever progress was made, including on
        # KeyboardInterrupt or an unhandled error partway through.
        _save_json_cache(cache_path, cache)

    return {mid: EmailMessage(**cache[mid]) for mid in message_ids if mid in cache}


def load_cached_emails(cache_path: str = "email_cache.json") -> dict[str, EmailMessage]:
    """
    Load full messages previously saved by fetch_emails() straight from
    disk, with no Gmail API calls at all. Useful for working offline with
    data already fetched, or for a quick look at cache progress while a
    fetch_emails() run is still going in another process.
    """
    cache = _load_json_cache(cache_path)
    return {mid: EmailMessage(**data) for mid, data in cache.items()}


def fetch_senders(
    max_results: int = 1000,
    query: str | None = None,
    label_ids: list[str] | None = None,
    cache_path: str = "sender_cache.json",
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
    save_every: int = 25,
) -> dict[str, SenderInfo]:
    """
    Fetch the sender's display name and email address for up to `max_results`
    messages, at minimal cost per message (only the 'From' header is
    requested, not the full message body).

    Progress is cached in a local JSON file (`cache_path`) keyed by message
    ID. On each run, message IDs already present in the cache are skipped
    entirely, so you can Ctrl+C at any point and re-run the same call later
    to pick up exactly where you left off, without re-spending quota on
    messages you've already fetched.

    Parameters
    ----------
    max_results  : maximum number of messages to consider (across the whole
                   cache, not just this run).
    query        : optional Gmail search query (same syntax as fetch_emails).
    label_ids    : optional list of label IDs to filter by, e.g. ["INBOX"].
    cache_path   : JSON file used to persist {message_id: {name, email, raw}}.
                   Reused and extended on subsequent calls.
    save_every   : flush the cache to disk after this many *new* lookups
                   (in addition to always saving on exit/interrupt/error).

    Returns
    -------
    dict[str, SenderInfo] mapping message ID -> SenderInfo, covering every
    ID in the requested range (whether served fresh or from cache).
    """
    cache = _load_json_cache(cache_path)
    creds = get_credentials(credentials_path, token_path)
    service = build_gmail_service(creds)
    limiter = QuotaRateLimiter()

    # 1. Collect the message IDs in scope (messages.list costs 5 units/call).
    message_ids: list[str] = []
    page_token: str | None = None
    while len(message_ids) < max_results:
        remaining = max_results - len(message_ids)
        list_kwargs = {"userId": "me", "maxResults": min(remaining, 500)}
        if query:
            list_kwargs["q"] = query
        if label_ids:
            list_kwargs["labelIds"] = label_ids
        if page_token:
            list_kwargs["pageToken"] = page_token

        limiter.consume(5)
        response = _execute_with_backoff(service.users().messages().list(**list_kwargs))
        refs = response.get("messages", [])
        if not refs:
            break
        message_ids.extend(ref["id"] for ref in refs)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    # 2. Fetch the sender of each message not already cached
    #    (messages.get costs 20 units/call regardless of format).
    to_fetch = [mid for mid in message_ids if mid not in cache]
    print(
        f"{len(message_ids)} messages in range, {len(cache)} already cached, "
        f"{len(to_fetch)} left to fetch."
    )

    fetched_since_save = 0
    try:
        for i, mid in enumerate(to_fetch, start=1):
            limiter.consume(20)
            message = _execute_with_backoff(
                service.users()
                .messages()
                .get(userId="me", id=mid, format="metadata", metadataHeaders=["From"])
            )
            headers = message.get("payload", {}).get("headers", [])
            raw_from = _header(headers, "From")
            name, email_addr = parseaddr(raw_from)
            name = _decode_header_value(name) or email_addr

            cache[mid] = {"name": name, "email": email_addr, "raw": raw_from}
            fetched_since_save += 1

            if fetched_since_save >= save_every:
                _save_json_cache(cache_path, cache)
                fetched_since_save = 0
                print(f"  ...{i}/{len(to_fetch)} fetched, cache saved.")
    finally:
        # Always persist whatever progress was made, including on
        # KeyboardInterrupt or an unhandled error partway through.
        _save_json_cache(cache_path, cache)

    return {
        mid: SenderInfo(
            message_id=mid,
            name=cache[mid]["name"],
            email=cache[mid]["email"],
            raw=cache[mid]["raw"],
        )
        for mid in message_ids
        if mid in cache
    }


def _parse_label(raw: dict) -> Label:
    color = raw.get("color", {}) or {}
    return Label(
        id=raw.get("id", ""),
        name=raw.get("name", ""),
        type=raw.get("type", ""),
        message_list_visibility=raw.get("messageListVisibility"),
        label_list_visibility=raw.get("labelListVisibility"),
        text_color=color.get("textColor"),
        background_color=color.get("backgroundColor"),
    )


def list_labels(
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
) -> list[Label]:
    """
    List every label on the account: built-in system labels (INBOX, SENT,
    IMPORTANT, UNREAD, ...) plus any custom user labels, each with its ID,
    name, type, and visibility/color settings.
    """
    creds = get_credentials(credentials_path, token_path)
    service = build_gmail_service(creds)
    response = _execute_with_backoff(service.users().labels().list(userId="me"))
    return [_parse_label(raw) for raw in response.get("labels", [])]


def get_label_map(
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
) -> dict[str, str]:
    """
    Convenience wrapper: {label name: label id} for every label on the
    account. Handy since apply_labels()/batch_apply_labels() take IDs,
    not names.
    """
    return {label.name: label.id for label in list_labels(credentials_path, token_path)}


def create_label(
    name: str,
    label_list_visibility: str = "labelShow",
    message_list_visibility: str = "show",
    text_color: str | None = None,
    background_color: str | None = None,
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
) -> Label:
    """
    Create a new user label. `name` can use "/" to nest labels, e.g.
    "Receipts/2026" shows as a nested label under "Receipts" in the Gmail UI.

    label_list_visibility   : "labelShow", "labelShowIfUnread", or "labelHide"
    message_list_visibility : "show" or "hide"
    text_color / background_color : optional hex strings (e.g. "#ffffff").
        Gmail only accepts colors from a fixed palette -- if Google rejects
        a custom pair with a 400 error, check the allowed list in the
        Gmail API docs for users.labels.
    """
    creds = get_credentials(credentials_path, token_path)
    service = build_gmail_service(creds)

    body: dict = {
        "name": name,
        "labelListVisibility": label_list_visibility,
        "messageListVisibility": message_list_visibility,
    }
    if text_color or background_color:
        body["color"] = {}
        if text_color:
            body["color"]["textColor"] = text_color
        if background_color:
            body["color"]["backgroundColor"] = background_color

    raw = _execute_with_backoff(service.users().labels().create(userId="me", body=body))
    return _parse_label(raw)


def get_or_create_label(
    name: str,
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
    **create_kwargs,
) -> Label:
    """
    Look up a label by exact name, creating it if it doesn't exist yet.
    Convenience wrapper around list_labels() + create_label() for the
    common "make sure this label exists" case. Extra keyword arguments
    (label_list_visibility, text_color, background_color, ...) are
    forwarded to create_label() if a new label needs to be made.
    """
    for label in list_labels(credentials_path, token_path):
        if label.name == name:
            return label
    return create_label(
        name, credentials_path=credentials_path, token_path=token_path, **create_kwargs
    )


def update_label(
    label_id: str,
    name: str | None = None,
    label_list_visibility: str | None = None,
    message_list_visibility: str | None = None,
    text_color: str | None = None,
    background_color: str | None = None,
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
) -> Label:
    """
    Update an existing user label (rename, restyle, or change visibility).
    Only the fields you pass are changed; omitted fields are left as-is.
    Raises an HttpError (400) if label_id refers to a system label (INBOX,
    SENT, ...), since those can't be renamed or restyled.
    """
    creds = get_credentials(credentials_path, token_path)
    service = build_gmail_service(creds)

    body: dict = {}
    if name is not None:
        body["name"] = name
    if label_list_visibility is not None:
        body["labelListVisibility"] = label_list_visibility
    if message_list_visibility is not None:
        body["messageListVisibility"] = message_list_visibility
    if text_color or background_color:
        body["color"] = {}
        if text_color:
            body["color"]["textColor"] = text_color
        if background_color:
            body["color"]["backgroundColor"] = background_color

    raw = _execute_with_backoff(
        service.users().labels().patch(userId="me", id=label_id, body=body)
    )
    return _parse_label(raw)


def delete_label(
    label_id: str,
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
) -> None:
    """
    Permanently delete a user label. Raises an HttpError (400) if label_id
    refers to a system label (INBOX, SENT, ...), which can't be deleted.
    """
    creds = get_credentials(credentials_path, token_path)
    service = build_gmail_service(creds)
    _execute_with_backoff(service.users().labels().delete(userId="me", id=label_id))


def apply_labels(
    message_id: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
) -> list[str]:
    """
    Add and/or remove labels on a single message in one call. `add`/`remove`
    take label IDs (not names) -- use list_labels() or get_label_map() to
    resolve names to IDs first. Returns the message's resulting list of
    label IDs.
    """
    creds = get_credentials(credentials_path, token_path)
    service = build_gmail_service(creds)

    body: dict = {}
    if add:
        body["addLabelIds"] = add
    if remove:
        body["removeLabelIds"] = remove

    raw = _execute_with_backoff(
        service.users().messages().modify(userId="me", id=message_id, body=body)
    )
    return raw.get("labelIds", []) or []


def batch_apply_labels(
    message_ids: list[str],
    add: list[str] | None = None,
    remove: list[str] | None = None,
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
) -> None:
    """
    Add and/or remove labels across up to 1,000 messages in a single
    request -- 50 quota units total, versus 5 units per message if you
    called apply_labels() in a loop. For more than 1,000 IDs, split
    message_ids into chunks of 1,000 and call this once per chunk.

    Re-applying a label a message already has, or removing one it doesn't
    have, is a no-op rather than an error.
    """
    if len(message_ids) > 1000:
        raise ValueError(
            f"batchModify accepts at most 1000 message IDs per call, got {len(message_ids)}. "
            "Split message_ids into chunks of 1000 and call this once per chunk."
        )

    creds = get_credentials(credentials_path, token_path)
    service = build_gmail_service(creds)

    body: dict = {"ids": message_ids}
    if add:
        body["addLabelIds"] = add
    if remove:
        body["removeLabelIds"] = remove

    _execute_with_backoff(
        service.users().messages().batchModify(userId="me", body=body)
    )


if __name__ == "__main__":
    # Quick manual test: fetch the 5 most recent messages in the inbox.
    for m in fetch_emails(max_results=5, label_ids=["INBOX"]).values():
        print("-" * 60)
        print(f"From:    {m.sender}")
        print(f"Subject: {m.subject}")
        print(f"Date:    {m.date}")
        print(f"Snippet: {m.snippet}")

    # Label demo (uncomment to try):
    # label = get_or_create_label("Test Label")
    # print("Labels on account:", [l.name for l in list_labels()])
