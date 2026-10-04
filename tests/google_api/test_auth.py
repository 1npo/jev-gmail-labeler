import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from google.auth.exceptions import RefreshError, TransportError

from jev_gmail_labeler import logging_setup
from jev_gmail_labeler.errors import AuthError, ConfigError
from jev_gmail_labeler.google_api import auth

TOKEN = Path('/cfg/token.json')


def token_info(scopes=auth.SCOPES, **extra):
    info = {
        'token': 'access-token-123',
        'refresh_token': 'refresh-token-456',
        'client_id': 'cid',
        'client_secret': 'csecret',
        'scopes': list(scopes),
    }
    info.update(extra)
    return info


@pytest.fixture(autouse=True)
def clean_secrets(monkeypatch):
    monkeypatch.setattr(logging_setup, '_secrets', set())


@pytest.fixture
def fake_files(monkeypatch):
    m = SimpleNamespace(read_json=MagicMock(), write=MagicMock())
    monkeypatch.setattr(auth.files, 'read_json', m.read_json)
    monkeypatch.setattr(auth.files, 'write_text_atomic', m.write)
    return m


@pytest.fixture
def fake_creds(monkeypatch):
    creds = MagicMock()
    creds.has_scopes.return_value = True
    creds.valid = True
    creds.token = 'access-token-123'
    creds.refresh_token = 'refresh-token-456'
    creds.to_json.return_value = '{"x": 1}'
    cls = MagicMock()
    cls.from_authorized_user_info.return_value = creds
    monkeypatch.setattr(auth, 'Credentials', cls)
    return creds


def test_scopes():
    assert auth.SCOPES == (
        'https://www.googleapis.com/auth/gmail.modify',
        'https://www.googleapis.com/auth/pubsub',
    )


def test_load_ok_registers_secrets(fake_files, fake_creds):
    fake_files.read_json.return_value = token_info()
    assert auth.load_credentials(TOKEN) is fake_creds
    fake_creds.refresh.assert_not_called()
    assert logging_setup._secrets == {'access-token-123', 'refresh-token-456'}


def test_load_missing_file(fake_files):
    fake_files.read_json.side_effect = FileNotFoundError
    with pytest.raises(AuthError, match='No token at /cfg/token.json'):
        auth.load_credentials(TOKEN)


def test_load_invalid_json(fake_files):
    fake_files.read_json.side_effect = ValueError('bad json')
    with pytest.raises(AuthError, match='bad json'):
        auth.load_credentials(TOKEN)


def test_load_malformed_token(fake_files):
    fake_files.read_json.return_value = {'token': 'x'}
    with pytest.raises(AuthError, match='Token file is invalid'):
        auth.load_credentials(TOKEN)


def test_missing_pubsub_scope_detected_with_real_credentials(fake_files):
    old = token_info(scopes=['https://www.googleapis.com/auth/gmail.modify'])
    fake_files.read_json.return_value = old
    with pytest.raises(AuthError, match='missing required scopes'):
        auth.load_credentials(TOKEN)


def test_token_without_recorded_scopes_rejected(fake_files):
    info = token_info()
    del info['scopes']
    fake_files.read_json.return_value = info
    with pytest.raises(AuthError, match='missing required scopes'):
        auth.load_credentials(TOKEN)


def test_valid_real_credentials_load(fake_files):
    fake_files.read_json.return_value = token_info(expiry='2999-01-01T00:00:00Z')
    creds = auth.load_credentials(TOKEN)
    assert creds.refresh_token == 'refresh-token-456'


def test_scopes_missing_on_fake(fake_files, fake_creds):
    fake_files.read_json.return_value = token_info()
    fake_creds.has_scopes.return_value = False
    with pytest.raises(AuthError, match='missing required scopes'):
        auth.load_credentials(TOKEN)


def test_expired_token_refreshes_and_saves_0600(fake_files, fake_creds, monkeypatch):
    request = MagicMock()
    monkeypatch.setattr(auth, 'Request', request)
    fake_files.read_json.return_value = token_info()
    fake_creds.valid = False
    auth.load_credentials(TOKEN)
    fake_creds.refresh.assert_called_once_with(request.return_value)
    fake_files.write.assert_called_once_with(TOKEN, '{"x": 1}', mode=0o600)


def test_expired_without_refresh_token(fake_files, fake_creds):
    fake_files.read_json.return_value = token_info()
    fake_creds.valid = False
    fake_creds.refresh_token = None
    with pytest.raises(AuthError, match='no refresh token'):
        auth.load_credentials(TOKEN)


def test_refresh_error_is_auth_error(fake_files, fake_creds):
    fake_files.read_json.return_value = token_info()
    fake_creds.valid = False
    fake_creds.refresh.side_effect = RefreshError('invalid_grant')
    with pytest.raises(AuthError, match='invalid_grant') as e:
        auth.load_credentials(TOKEN)
    assert e.value.transient is False
    fake_files.write.assert_not_called()


def test_refresh_transport_error_is_transient(fake_files, fake_creds):
    fake_files.read_json.return_value = token_info()
    fake_creds.valid = False
    fake_creds.refresh.side_effect = TransportError('offline')
    with pytest.raises(AuthError) as e:
        auth.load_credentials(TOKEN)
    assert e.value.transient is True


def test_save_credentials(fake_files, fake_creds):
    auth.save_credentials(fake_creds, TOKEN)
    fake_files.write.assert_called_once_with(TOKEN, '{"x": 1}', mode=0o600)


def test_consent_flow(fake_files, monkeypatch):
    client_config = {'installed': {'client_id': 'x'}}
    fake_files.read_json.return_value = client_config
    creds = MagicMock(token='access-token-123', refresh_token='refresh-token-456')
    creds.to_json.return_value = json.dumps({'a': 1})
    flow = MagicMock()
    flow.run_local_server.return_value = creds
    installed = MagicMock()
    installed.from_client_config.return_value = flow
    monkeypatch.setattr(auth, 'InstalledAppFlow', installed)

    result = auth.run_consent_flow(
        Path('/cfg/credentials.json'), TOKEN, port=8765, open_browser=False
    )

    assert result is creds
    installed.from_client_config.assert_called_once_with(client_config, list(auth.SCOPES))
    flow.run_local_server.assert_called_once_with(port=8765, open_browser=False)
    fake_files.write.assert_called_once_with(TOKEN, '{"a": 1}', mode=0o600)
    assert 'access-token-123' in logging_setup._secrets


def test_consent_flow_missing_credentials(fake_files):
    fake_files.read_json.side_effect = FileNotFoundError
    with pytest.raises(ConfigError, match='Desktop app'):
        auth.run_consent_flow(Path('/cfg/credentials.json'), TOKEN)


def test_consent_flow_bad_credentials_json(fake_files):
    fake_files.read_json.side_effect = ValueError('/cfg/credentials.json: invalid JSON')
    with pytest.raises(ConfigError, match='invalid JSON'):
        auth.run_consent_flow(Path('/cfg/credentials.json'), TOKEN)
