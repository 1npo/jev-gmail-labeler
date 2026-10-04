from unittest.mock import MagicMock

import pytest

from jev_gmail_labeler import app
from jev_gmail_labeler.errors import ConfigError


@pytest.fixture
def patched(monkeypatch):
    names = (
        'load_credentials',
        'GmailClient',
        'QuotaLimiter',
        'load_criteria',
        'JevClassifier',
        'resolve_typesafe_api_key',
        'Pipeline',
        'LabelManager',
        'Anonymizer',
        'PubSubPuller',
        'StateStore',
        'LabelerService',
    )
    mocks = {n: MagicMock(name=n) for n in names}
    for name, mock in mocks.items():
        monkeypatch.setattr(app, name, mock)
    mocks['resolve_typesafe_api_key'].return_value = 'key-123456'
    return mocks


def test_build_gmail(app_config, patched):
    gmail = app.build_gmail(app_config)
    patched['load_credentials'].assert_called_once_with(app_config.token_file)
    patched['QuotaLimiter'].assert_called_once_with(5000)
    patched['GmailClient'].assert_called_once_with(
        patched['load_credentials'].return_value,
        user_id='me',
        quota=patched['QuotaLimiter'].return_value,
    )
    assert gmail is patched['GmailClient'].return_value


def test_build_pipeline(app_config, patched):
    gmail = MagicMock()
    result = app.build_pipeline(app_config, gmail=gmail, environ={'K': 'v'})
    patched['load_criteria'].assert_called_once_with(app_config.criteria_file)
    patched['resolve_typesafe_api_key'].assert_called_once_with(app_config, {'K': 'v'})
    patched['JevClassifier'].assert_called_once_with(
        patched['load_criteria'].return_value,
        api_key='key-123456',
        model='jev-latest',
        timeout=10.0,
    )
    patched['Anonymizer'].assert_called_once_with('en_core_web_md')
    patched['LabelManager'].assert_called_once_with(gmail)
    kwargs = patched['Pipeline'].call_args.kwargs
    assert kwargs['gmail'] is gmail
    assert kwargs['max_body_chars'] == 8000
    assert result is patched['Pipeline'].return_value


def test_build_service_shares_credentials(app_config, patched):
    svc = app.build_service(app_config, environ={}, dry_run=True)
    patched['load_credentials'].assert_called_once()
    creds = patched['load_credentials'].return_value
    patched['PubSubPuller'].assert_called_once_with('projects/p/subscriptions/s', creds)
    patched['StateStore'].assert_called_once_with(app_config.state_db)
    kwargs = patched['LabelerService'].call_args.kwargs
    assert kwargs['dry_run'] is True
    assert kwargs['config'] is app_config
    assert svc is patched['LabelerService'].return_value


@pytest.mark.parametrize('missing', ['pubsub_topic', 'pubsub_subscription'])
def test_listen_requires_topic_and_subscription(app_config, patched, missing):
    from dataclasses import replace

    config = replace(app_config, **{missing: None})
    with pytest.raises(ConfigError, match=missing):
        app.build_service(config, environ={}, dry_run=False)
    patched['load_credentials'].assert_not_called()
