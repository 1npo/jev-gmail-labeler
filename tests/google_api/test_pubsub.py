from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from google.api_core import exceptions as gexc
from google.auth.exceptions import RefreshError

from jev_gmail_labeler.errors import AuthError, ConfigError, PubSubError
from jev_gmail_labeler.google_api.pubsub import (
    Notification,
    PubSubPuller,
    PulledMessage,
    parse_notification,
)

SUB = 'projects/p/subscriptions/s'


@pytest.fixture
def client():
    return MagicMock(name='subscriber')


@pytest.fixture
def puller(client):
    return PubSubPuller(SUB, credentials=None, client=client)


def test_parse_valid():
    data = b'{"emailAddress": "a@b.com", "historyId": 12}'
    assert parse_notification(data) == Notification('a@b.com', 12)


def test_parse_string_history_id():
    data = b'{"emailAddress": "a@b.com", "historyId": "12"}'
    assert parse_notification(data).history_id == 12


@pytest.mark.parametrize(
    'data',
    [
        b'not json',
        b'{"emailAddress": "a@b.com"}',
        b'{"historyId": 1}',
        b'[1, 2]',
        b'{"emailAddress": "a@b.com", "historyId": "x"}',
        b'{"emailAddress": 5, "historyId": 1}',
    ],
)
def test_parse_invalid(data):
    assert parse_notification(data) is None


def test_default_client_built(monkeypatch):
    sub = MagicMock()
    monkeypatch.setattr(
        'jev_gmail_labeler.google_api.pubsub.pubsub_v1.SubscriberClient', sub
    )
    PubSubPuller(SUB, credentials='creds')
    sub.assert_called_once_with(credentials='creds')


def test_pull_maps_messages(puller, client):
    received = SimpleNamespace(
        ack_id='a1', message=SimpleNamespace(data=b'{}', message_id='pm1')
    )
    client.pull.return_value = SimpleNamespace(received_messages=[received])
    assert puller.pull(5, 20.0) == [PulledMessage('a1', b'{}', 'pm1')]
    client.pull.assert_called_once_with(
        request={'subscription': SUB, 'max_messages': 5}, timeout=20.0
    )


def test_pull_deadline_exceeded_returns_empty(puller, client):
    client.pull.side_effect = gexc.DeadlineExceeded('slow')
    assert puller.pull(5, 1.0) == []


def test_pull_unauthenticated(puller, client):
    client.pull.side_effect = gexc.Unauthenticated('no')
    with pytest.raises(AuthError):
        puller.pull(5, 1.0)


@pytest.mark.parametrize('exc', [gexc.NotFound, gexc.PermissionDenied])
def test_pull_config_errors(puller, client, exc):
    client.pull.side_effect = exc('nope')
    with pytest.raises(ConfigError, match=f'Cannot read subscription {SUB}'):
        puller.pull(5, 1.0)


def test_pull_unavailable_is_transient(puller, client):
    client.pull.side_effect = gexc.ServiceUnavailable('down')
    with pytest.raises(PubSubError) as e:
        puller.pull(5, 1.0)
    assert e.value.transient


def test_pull_refresh_error(puller, client):
    client.pull.side_effect = RefreshError('revoked')
    with pytest.raises(AuthError):
        puller.pull(5, 1.0)


def test_ack_request(puller, client):
    puller.ack(['a', 'b'])
    client.acknowledge.assert_called_once_with(
        request={'subscription': SUB, 'ack_ids': ['a', 'b']}
    )


def test_nack_request(puller, client):
    puller.nack(['a'])
    client.modify_ack_deadline.assert_called_once_with(
        request={'subscription': SUB, 'ack_ids': ['a'], 'ack_deadline_seconds': 0}
    )


def test_empty_ack_nack_make_no_call(puller, client):
    puller.ack([])
    puller.nack([])
    client.acknowledge.assert_not_called()
    client.modify_ack_deadline.assert_not_called()


def test_close(puller, client):
    puller.close()
    client.close.assert_called_once_with()
