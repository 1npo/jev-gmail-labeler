"""Pull Gmail push notifications from a Pub/Sub subscription."""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from google.api_core import exceptions as gexc
from google.auth.exceptions import RefreshError
from google.cloud import pubsub_v1

from jev_gmail_labeler.errors import AuthError, ConfigError, PubSubError

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PulledMessage:
    """One message pulled from the subscription."""

    ack_id: str
    data: bytes
    message_id: str


@dataclass(frozen=True, slots=True)
class Notification:
    """The content of a Gmail push notification."""

    email_address: str
    history_id: int


def parse_notification(data: bytes) -> Notification | None:
    """Decode a Gmail notification; return None if it is not a valid one."""
    try:
        payload = json.loads(data)
        email = payload['emailAddress']
        history_id = int(payload['historyId'])
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(email, str):
        return None
    return Notification(email_address=email, history_id=history_id)


class PubSubPuller:
    """Unary-pull wrapper around ``SubscriberClient`` with error mapping."""

    def __init__(
        self, subscription: str, credentials: Any, *, client: Any = None
    ) -> None:
        self._subscription = subscription
        self._client = client or pubsub_v1.SubscriberClient(credentials=credentials)

    def _call(self, func: Any, request: dict[str, Any], **kwargs: Any) -> Any:
        try:
            return func(request=request, **kwargs)
        except gexc.DeadlineExceeded:
            raise
        except gexc.Unauthenticated as e:
            raise AuthError(f'Pub/Sub rejected the credentials: {e}') from e
        except (gexc.NotFound, gexc.PermissionDenied) as e:
            raise ConfigError(
                f'Cannot read subscription {self._subscription}: {e}'
            ) from e
        except gexc.GoogleAPICallError as e:
            raise PubSubError(f'{type(e).__name__}: {e}', transient=True) from e
        except RefreshError as e:
            raise AuthError(str(e)) from e

    def pull(self, max_messages: int, timeout: float) -> list[PulledMessage]:
        """Pull up to ``max_messages``; returns [] when nothing arrives in time."""
        try:
            response = self._call(
                self._client.pull,
                {'subscription': self._subscription, 'max_messages': max_messages},
                timeout=timeout,
            )
        except gexc.DeadlineExceeded:
            return []
        return [
            PulledMessage(
                ack_id=m.ack_id, data=m.message.data, message_id=m.message.message_id
            )
            for m in response.received_messages
        ]

    def ack(self, ack_ids: Sequence[str]) -> None:
        """Acknowledge messages (no-op for an empty list)."""
        if ack_ids:
            self._call(
                self._client.acknowledge,
                {'subscription': self._subscription, 'ack_ids': list(ack_ids)},
            )

    def nack(self, ack_ids: Sequence[str]) -> None:
        """Make messages available for redelivery immediately."""
        if ack_ids:
            self._call(
                self._client.modify_ack_deadline,
                {
                    'subscription': self._subscription,
                    'ack_ids': list(ack_ids),
                    'ack_deadline_seconds': 0,
                },
            )

    def close(self) -> None:
        """Close the underlying client."""
        self._client.close()
