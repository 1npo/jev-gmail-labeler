"""Thin Gmail API client with quota pacing, retries and typed errors."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httplib2
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from jev_gmail_labeler.errors import (
    AuthError,
    GmailError,
    HistoryExpiredError,
    MessageNotFoundError,
)
from jev_gmail_labeler.google_api.message_parser import parse_message
from jev_gmail_labeler.google_api.quota import COSTS, QuotaLimiter
from jev_gmail_labeler.models import EmailMessage

MAX_PAGE_SIZE = 500


@dataclass(frozen=True, slots=True)
class Profile:
    """The mailbox address and its current history id."""

    email_address: str
    history_id: int


@dataclass(frozen=True, slots=True)
class HistoryPage:
    """Message ids added since a history id, and the new history id."""

    message_ids: tuple[str, ...]
    history_id: int


@dataclass(frozen=True, slots=True)
class WatchResponse:
    """Result of starting a mailbox watch."""

    history_id: int
    expiration_ms: int


@dataclass(frozen=True, slots=True)
class GmailLabel:
    """A Gmail label."""

    id: str
    name: str
    type: str


class GmailClient:
    """Gmail API calls used by the labeler."""

    def __init__(
        self,
        credentials: Any,
        *,
        user_id: str = 'me',
        quota: QuotaLimiter | None = None,
        num_retries: int = 5,
        service: Any = None,
    ) -> None:
        self._user_id = user_id
        self._quota = quota or QuotaLimiter()
        self._num_retries = num_retries
        self._service = service or build(
            'gmail', 'v1', credentials=credentials, cache_discovery=False
        )

    def _users(self) -> Any:
        return self._service.users()

    def _execute(
        self, request: Any, cost_key: str, *, not_found: type[GmailError] = GmailError
    ) -> Any:
        self._quota.consume(COSTS[cost_key])
        try:
            return request.execute(num_retries=self._num_retries)
        except HttpError as e:
            status = int(e.resp.status)
            message = f'Gmail API {cost_key} failed ({status}): {e.reason}'
            if status == 401:
                raise AuthError(message) from e
            if status == 404:
                raise not_found(message, status=status, transient=False) from e
            transient = status == 429 or status >= 500
            raise GmailError(message, status=status, transient=transient) from e
        except RefreshError as e:
            raise AuthError(f'Google token refresh failed: {e}') from e
        except (OSError, httplib2.HttpLib2Error) as e:
            raise GmailError(
                f'Gmail API {cost_key} network error: {type(e).__name__}: {e}',
                transient=True,
            ) from e

    def get_profile(self) -> Profile:
        """Return the mailbox address and current history id."""
        data = self._execute(self._users().getProfile(userId=self._user_id), 'getProfile')
        return Profile(data['emailAddress'], int(data['historyId']))

    def list_message_ids(self, *, query: str | None, max_results: int) -> list[str]:
        """Return up to ``max_results`` message ids, newest first."""
        ids: list[str] = []
        token: str | None = None
        while len(ids) < max_results:
            params: dict[str, Any] = {
                'userId': self._user_id,
                'maxResults': min(MAX_PAGE_SIZE, max_results - len(ids)),
            }
            if query:
                params['q'] = query
            if token:
                params['pageToken'] = token
            page = self._execute(self._users().messages().list(**params), 'messages.list')
            ids.extend(m['id'] for m in page.get('messages', []))
            token = page.get('nextPageToken')
            if not token:
                break
        return ids[:max_results]

    def get_message(self, message_id: str) -> EmailMessage:
        """Fetch and parse one message."""
        request = (
            self._users()
            .messages()
            .get(userId=self._user_id, id=message_id, format='full')
        )
        raw = self._execute(request, 'messages.get', not_found=MessageNotFoundError)
        return parse_message(raw)

    def list_history(
        self, start_history_id: int, *, label_id: str = 'INBOX'
    ) -> HistoryPage:
        """Return ids of messages added to ``label_id`` since ``start_history_id``."""
        ids: dict[str, None] = {}
        token: str | None = None
        history_id = start_history_id
        while True:
            params: dict[str, Any] = {
                'userId': self._user_id,
                'startHistoryId': str(start_history_id),
                'historyTypes': ['messageAdded'],
                'labelId': label_id,
                'maxResults': MAX_PAGE_SIZE,
            }
            if token:
                params['pageToken'] = token
            page = self._execute(
                self._users().history().list(**params),
                'history.list',
                not_found=HistoryExpiredError,
            )
            for record in page.get('history', []):
                for added in record.get('messagesAdded', []):
                    ids.setdefault(added['message']['id'])
            history_id = int(page.get('historyId', history_id))
            token = page.get('nextPageToken')
            if not token:
                return HistoryPage(tuple(ids), history_id)

    def watch(self, topic: str, label_ids: Sequence[str] = ('INBOX',)) -> WatchResponse:
        """Start (or renew) push notifications for the mailbox."""
        body = {
            'topicName': topic,
            'labelIds': list(label_ids),
            'labelFilterBehavior': 'INCLUDE',
        }
        data = self._execute(
            self._users().watch(userId=self._user_id, body=body), 'watch'
        )
        return WatchResponse(int(data['historyId']), int(data['expiration']))

    def stop_watch(self) -> None:
        """Stop push notifications."""
        self._execute(self._users().stop(userId=self._user_id), 'stop')

    def list_labels(self) -> list[GmailLabel]:
        """Return every label in the mailbox."""
        data = self._execute(
            self._users().labels().list(userId=self._user_id), 'labels.list'
        )
        return [
            GmailLabel(x['id'], x['name'], x.get('type', 'user'))
            for x in data.get('labels', [])
        ]

    def create_label(self, name: str) -> GmailLabel:
        """Create a visible user label."""
        body = {
            'name': name,
            'labelListVisibility': 'labelShow',
            'messageListVisibility': 'show',
        }
        data = self._execute(
            self._users().labels().create(userId=self._user_id, body=body),
            'labels.create',
        )
        return GmailLabel(data['id'], data['name'], data.get('type', 'user'))

    def modify_labels(
        self, message_id: str, *, add: Sequence[str], remove: Sequence[str] = ()
    ) -> None:
        """Add and remove labels on one message."""
        body = {'addLabelIds': list(add), 'removeLabelIds': list(remove)}
        request = (
            self._users()
            .messages()
            .modify(userId=self._user_id, id=message_id, body=body)
        )
        self._execute(request, 'messages.modify')
