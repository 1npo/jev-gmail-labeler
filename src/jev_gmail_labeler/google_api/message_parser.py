"""Turn Gmail API ``format=full`` messages into :class:`EmailMessage`."""

import base64
import codecs
import contextlib
import re
from collections.abc import Mapping
from email.header import decode_header, make_header
from html import unescape
from typing import Any

from bs4 import BeautifulSoup

from jev_gmail_labeler.models import EmailMessage

_INVISIBLE = dict.fromkeys(map(ord, '​‌‍‎‏⁠﻿­͏'))
_CHARSET_RE = re.compile(r'charset\s*=\s*["\']?([\w.:-]+)', re.IGNORECASE)


def decode_header_value(value: str) -> str:
    """Decode RFC 2047 encoded words; return the input unchanged on failure."""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _clean_text(text: str) -> str:
    text = text.translate(_INVISIBLE).replace(' ', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def html_to_text(html: str) -> str:
    """Convert HTML to cleaned plain text."""
    soup = BeautifulSoup(html, 'lxml')
    for tag in soup(['script', 'style', 'head', 'title']):
        tag.decompose()
    return _clean_text(unescape(soup.get_text('\n')))


def _decode_part(part: Mapping[str, Any]) -> str:
    data = part.get('body', {}).get('data', '')
    raw = base64.urlsafe_b64decode(data + '=' * (-len(data) % 4))
    charset = 'utf-8'
    for h in part.get('headers', []):
        if h.get('name', '').lower() == 'content-type':
            m = _CHARSET_RE.search(h.get('value', ''))
            if m:
                with contextlib.suppress(LookupError):
                    charset = codecs.lookup(m.group(1)).name
    return raw.decode(charset, errors='replace')


def _collect(
    part: Mapping[str, Any], html_parts: list[str], plain_parts: list[str]
) -> None:
    if part.get('filename') or part.get('body', {}).get('attachmentId'):
        return
    for child in part.get('parts', []):
        _collect(child, html_parts, plain_parts)
    mime = part.get('mimeType', '')
    if part.get('body', {}).get('data'):
        if mime == 'text/html':
            html_parts.append(_decode_part(part))
        elif mime == 'text/plain':
            plain_parts.append(_decode_part(part))


def extract_body_text(payload: Mapping[str, Any]) -> str:
    """Return cleaned body text, preferring HTML parts; '' if there is no text part."""
    html_parts: list[str] = []
    plain_parts: list[str] = []
    _collect(payload, html_parts, plain_parts)
    text = ''
    if html_parts:
        text = html_to_text('\n'.join(html_parts))
    if not text and plain_parts:
        text = _clean_text('\n'.join(plain_parts))
    return text


def parse_message(raw: Mapping[str, Any]) -> EmailMessage:
    """Build an :class:`EmailMessage` from a Gmail API message resource."""
    payload = raw.get('payload', {})
    headers = {
        h.get('name', '').lower(): decode_header_value(h.get('value', ''))
        for h in payload.get('headers', [])
    }
    snippet = raw.get('snippet', '')
    body = extract_body_text(payload) or _clean_text(unescape(snippet))
    return EmailMessage(
        id=raw['id'],
        thread_id=raw.get('threadId', ''),
        history_id=raw.get('historyId', ''),
        internal_date_ms=int(raw.get('internalDate', 0)),
        label_ids=tuple(raw.get('labelIds', [])),
        sender=headers.get('from', ''),
        to=headers.get('to', ''),
        cc=headers.get('cc', ''),
        reply_to=headers.get('reply-to', ''),
        date=headers.get('date', ''),
        subject=headers.get('subject', ''),
        snippet=snippet,
        body_text=body,
    )
