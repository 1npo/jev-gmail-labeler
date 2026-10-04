from unittest.mock import MagicMock

import httplib2
import pytest
from google.auth.exceptions import RefreshError

from jev_gmail_labeler.errors import (
    AuthError,
    GmailError,
    HistoryExpiredError,
    MessageNotFoundError,
)
from jev_gmail_labeler.google_api import gmail
from jev_gmail_labeler.google_api.gmail import GmailClient, Profile
from jev_gmail_labeler.google_api.quota import COSTS, QuotaLimiter


@pytest.fixture
def quota():
    return MagicMock(spec=QuotaLimiter)


@pytest.fixture
def client(gmail_service, quota):
    return GmailClient(None, service=gmail_service, quota=quota)


def respond(api, service, path, *results):
    """Make ``service.users().<path>(...).execute`` return (or raise) in turn."""
    request = api(service, path).return_value
    request.execute.side_effect = list(results)
    return request


def test_builds_service_when_not_injected(monkeypatch):
    build = MagicMock()
    monkeypatch.setattr(gmail, 'build', build)
    creds = object()
    GmailClient(creds)
    build.assert_called_once_with('gmail', 'v1', credentials=creds, cache_discovery=False)


def test_get_profile(client, gmail_service, api, quota):
    respond(
        api, gmail_service, 'getProfile', {'emailAddress': 'me@x.com', 'historyId': '77'}
    )
    assert client.get_profile() == Profile('me@x.com', 77)
    api(gmail_service, 'getProfile').assert_called_once_with(userId='me')
    quota.consume.assert_called_once_with(COSTS['getProfile'])


def test_user_id_used(gmail_service, api):
    c = GmailClient(None, service=gmail_service, user_id='u@x.com', quota=MagicMock())
    respond(api, gmail_service, 'getProfile', {'emailAddress': 'a', 'historyId': '1'})
    c.get_profile()
    api(gmail_service, 'getProfile').assert_called_once_with(userId='u@x.com')


def test_num_retries_passed(client, gmail_service, api):
    request = respond(
        api, gmail_service, 'getProfile', {'emailAddress': 'a', 'historyId': '1'}
    )
    client.get_profile()
    request.execute.assert_called_once_with(num_retries=5)


def test_list_message_ids_single_page(client, gmail_service, api, quota):
    respond(api, gmail_service, 'messages.list', {'messages': [{'id': 'a'}, {'id': 'b'}]})
    assert client.list_message_ids(query='in:inbox', max_results=10) == ['a', 'b']
    api(gmail_service, 'messages.list').assert_called_once_with(
        userId='me', q='in:inbox', maxResults=10
    )
    quota.consume.assert_called_once_with(COSTS['messages.list'])


def test_list_message_ids_paginates_and_caps(client, gmail_service, api):
    respond(
        api,
        gmail_service,
        'messages.list',
        {'messages': [{'id': 'a'}, {'id': 'b'}], 'nextPageToken': 'p2'},
        {'messages': [{'id': 'c'}, {'id': 'd'}], 'nextPageToken': 'p3'},
    )
    assert client.list_message_ids(query=None, max_results=3) == ['a', 'b', 'c']
    calls = api(gmail_service, 'messages.list').call_args_list
    assert calls[0].kwargs == {'userId': 'me', 'maxResults': 3}
    assert calls[1].kwargs == {'userId': 'me', 'maxResults': 1, 'pageToken': 'p2'}
    assert len(calls) == 2


def test_list_message_ids_page_size_capped_at_500(client, gmail_service, api):
    respond(api, gmail_service, 'messages.list', {})
    assert client.list_message_ids(query='x', max_results=900) == []
    assert api(gmail_service, 'messages.list').call_args.kwargs['maxResults'] == 500


def test_get_message(client, gmail_service, api, make_raw_message, quota):
    respond(api, gmail_service, 'messages.get', make_raw_message(plain='hi'))
    m = client.get_message('m1')
    assert m.body_text == 'hi'
    api(gmail_service, 'messages.get').assert_called_once_with(
        userId='me', id='m1', format='full'
    )
    quota.consume.assert_called_once_with(20)


def test_get_message_404(client, gmail_service, api, make_http_error):
    respond(api, gmail_service, 'messages.get', make_http_error(404))
    with pytest.raises(MessageNotFoundError) as e:
        client.get_message('m1')
    assert e.value.status == 404
    assert e.value.transient is False


def test_list_history_collects_dedupes_and_uses_last_history_id(
    client, gmail_service, api
):
    respond(
        api,
        gmail_service,
        'history.list',
        {
            'history': [
                {'messagesAdded': [{'message': {'id': 'a'}}, {'message': {'id': 'b'}}]},
                {'labelsAdded': []},
            ],
            'nextPageToken': 'p2',
            'historyId': '200',
        },
        {
            'history': [
                {'messagesAdded': [{'message': {'id': 'a'}}, {'message': {'id': 'c'}}]}
            ],
            'historyId': '300',
        },
    )
    page = client.list_history(150)
    assert page.message_ids == ('a', 'b', 'c')
    assert page.history_id == 300
    calls = api(gmail_service, 'history.list').call_args_list
    assert calls[0].kwargs == {
        'userId': 'me',
        'startHistoryId': '150',
        'historyTypes': ['messageAdded'],
        'labelId': 'INBOX',
        'maxResults': 500,
    }
    assert calls[1].kwargs['pageToken'] == 'p2'


def test_list_history_empty_keeps_start_id(client, gmail_service, api):
    respond(api, gmail_service, 'history.list', {})
    page = client.list_history(150, label_id='X')
    assert (page.message_ids, page.history_id) == ((), 150)
    assert api(gmail_service, 'history.list').call_args.kwargs['labelId'] == 'X'


def test_list_history_404_is_expired(client, gmail_service, api, make_http_error):
    respond(api, gmail_service, 'history.list', make_http_error(404))
    with pytest.raises(HistoryExpiredError):
        client.list_history(1)


def test_watch(client, gmail_service, api):
    respond(
        api, gmail_service, 'watch', {'historyId': '5', 'expiration': '1700000000000'}
    )
    resp = client.watch('projects/p/topics/t')
    assert (resp.history_id, resp.expiration_ms) == (5, 1_700_000_000_000)
    api(gmail_service, 'watch').assert_called_once_with(
        userId='me',
        body={
            'topicName': 'projects/p/topics/t',
            'labelIds': ['INBOX'],
            'labelFilterBehavior': 'INCLUDE',
        },
    )


def test_stop_watch(client, gmail_service, api, quota):
    request = respond(api, gmail_service, 'stop', {})
    client.stop_watch()
    api(gmail_service, 'stop').assert_called_once_with(userId='me')
    request.execute.assert_called_once()
    quota.consume.assert_called_once_with(COSTS['stop'])


def test_list_labels(client, gmail_service, api):
    respond(
        api,
        gmail_service,
        'labels.list',
        {
            'labels': [
                {'id': 'L1', 'name': 'Jev/Receipt', 'type': 'user'},
                {'id': 'INBOX', 'name': 'INBOX'},
            ]
        },
    )
    labels = client.list_labels()
    assert [(x.id, x.name, x.type) for x in labels] == [
        ('L1', 'Jev/Receipt', 'user'),
        ('INBOX', 'INBOX', 'user'),
    ]


def test_list_labels_empty(client, gmail_service, api):
    respond(api, gmail_service, 'labels.list', {})
    assert client.list_labels() == []


def test_create_label(client, gmail_service, api, quota):
    respond(
        api, gmail_service, 'labels.create', {'id': 'L2', 'name': 'Jev', 'type': 'user'}
    )
    label = client.create_label('Jev')
    assert (label.id, label.name) == ('L2', 'Jev')
    api(gmail_service, 'labels.create').assert_called_once_with(
        userId='me',
        body={
            'name': 'Jev',
            'labelListVisibility': 'labelShow',
            'messageListVisibility': 'show',
        },
    )
    quota.consume.assert_called_once_with(COSTS['labels.create'])


def test_modify_labels(client, gmail_service, api, quota):
    respond(api, gmail_service, 'messages.modify', {})
    client.modify_labels('m1', add=['L1'], remove=['L2'])
    api(gmail_service, 'messages.modify').assert_called_once_with(
        userId='me', id='m1', body={'addLabelIds': ['L1'], 'removeLabelIds': ['L2']}
    )
    quota.consume.assert_called_once_with(COSTS['messages.modify'])


def test_modify_labels_remove_defaults_empty(client, gmail_service, api):
    respond(api, gmail_service, 'messages.modify', {})
    client.modify_labels('m1', add=['L1'])
    assert (
        api(gmail_service, 'messages.modify').call_args.kwargs['body']['removeLabelIds']
        == []
    )


def test_401_is_auth_error(client, gmail_service, api, make_http_error):
    respond(api, gmail_service, 'getProfile', make_http_error(401))
    with pytest.raises(AuthError):
        client.get_profile()


@pytest.mark.parametrize('status', [429, 500, 503])
def test_transient_statuses(client, gmail_service, api, make_http_error, status):
    respond(api, gmail_service, 'getProfile', make_http_error(status, 'slow down'))
    with pytest.raises(GmailError) as e:
        client.get_profile()
    assert e.value.transient is True
    assert e.value.status == status
    assert 'slow down' in str(e.value)


@pytest.mark.parametrize('status', [400, 403, 409])
def test_permanent_statuses(client, gmail_service, api, make_http_error, status):
    respond(api, gmail_service, 'getProfile', make_http_error(status))
    with pytest.raises(GmailError) as e:
        client.get_profile()
    assert e.value.transient is False
    assert e.value.status == status


def test_other_404_is_plain_gmail_error(client, gmail_service, api, make_http_error):
    respond(api, gmail_service, 'labels.list', make_http_error(404))
    with pytest.raises(GmailError) as e:
        client.list_labels()
    assert type(e.value) is GmailError


def test_refresh_error_is_auth_error(client, gmail_service, api):
    respond(api, gmail_service, 'getProfile', RefreshError('revoked'))
    with pytest.raises(AuthError):
        client.get_profile()


@pytest.mark.parametrize('exc', [OSError('reset'), httplib2.ServerNotFoundError('dns')])
def test_network_errors_are_transient(client, gmail_service, api, exc):
    respond(api, gmail_service, 'getProfile', exc)
    with pytest.raises(GmailError) as e:
        client.get_profile()
    assert e.value.transient is True
