import json
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jev_gmail_labeler import __version__, cli
from jev_gmail_labeler import config as config_module
from jev_gmail_labeler.criteria import EXAMPLE_CRITERIA, parse_criteria
from jev_gmail_labeler.errors import AuthError, ConfigError, CriteriaError, GmailError
from jev_gmail_labeler.models import PipelineResult, PipelineStatus


def make_result(mid='m1', status=PipelineStatus.WOULD_LABEL):
    return PipelineResult(
        message_id=mid,
        thread_id='t',
        date=None,
        sender=None,
        subject=None,
        status=status,
        skip_reason=None,
        classification=None,
        decision=None,
        applied=False,
        label_id=None,
        error=None,
        retryable=False,
        processed_at='2026-10-03T12:00:00+00:00',
    )


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Keep pytest's log handlers, ignore the real env, and never read a config file."""
    monkeypatch.setattr(cli, 'configure_logging', MagicMock())
    for key in list(os.environ):
        if key.startswith('JEV_LABELER_') or key == 'TYPESAFE_API_KEY':
            monkeypatch.delenv(key)
    monkeypatch.setattr(config_module.files, 'read_json', MagicMock(return_value={}))
    real_load = cli.load_config

    def load(**kwargs):
        kwargs['config_path'] = kwargs['config_path'] or Path('/fake/config.json')
        return real_load(**kwargs)

    monkeypatch.setattr(cli, 'load_config', load)


@pytest.fixture
def pipeline():
    mock = MagicMock(name='pipeline')
    mock.process_many.side_effect = lambda ids, apply, force: iter(
        [make_result(i) for i in ids]
    )

    @contextmanager
    def ctx():
        yield mock

    mock.ctx = ctx
    return mock


@pytest.fixture
def label_env(monkeypatch, pipeline):
    gmail = MagicMock(name='gmail')
    gmail.list_message_ids.return_value = ['a', 'b']
    monkeypatch.setattr(cli, 'build_gmail', MagicMock(return_value=gmail))
    monkeypatch.setattr(cli, 'build_pipeline', MagicMock(return_value=pipeline.ctx()))
    monkeypatch.setattr(cli, 'write_results', MagicMock())
    return gmail


# --- basics


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(['--version'])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


@pytest.mark.parametrize(
    'argv',
    [[], ['watch'], ['criteria'], ['config']],
)
def test_missing_command_exits_2(argv, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    assert e.value.code == 2


@pytest.mark.parametrize(
    'argv',
    [
        ['--help'],
        ['auth', '--help'],
        ['label', '--help'],
        ['listen', '--help'],
        ['watch', '--help'],
        ['watch', 'start', '--help'],
        ['watch', 'stop', '--help'],
        ['watch', 'status', '--help'],
        ['criteria', '--help'],
        ['criteria', 'validate', '--help'],
        ['criteria', 'example', '--help'],
        ['config', '--help'],
        ['config', 'show', '--help'],
    ],
)
def test_help_works(argv, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    assert e.value.code == 0
    assert 'usage' in capsys.readouterr().out


# --- auth


def test_auth(monkeypatch, capsys):
    flow = MagicMock()
    monkeypatch.setattr(cli, 'run_consent_flow', flow)
    assert (
        cli.main(['auth', '--port', '8765', '--no-browser', '--token-file', '/t.json'])
        == 0
    )
    flow.assert_called_once()
    assert flow.call_args.kwargs == {'port': 8765, 'open_browser': False}
    assert flow.call_args.args[1] == Path('/t.json')
    assert 'Saved token to /t.json' in capsys.readouterr().err


def test_auth_defaults_open_browser(monkeypatch):
    flow = MagicMock()
    monkeypatch.setattr(cli, 'run_consent_flow', flow)
    cli.main(['auth'])
    assert flow.call_args.kwargs == {'port': 0, 'open_browser': True}


# --- label


@pytest.mark.parametrize(
    'argv',
    [
        ['label', '--id', 'x', '--count', '2'],
        ['label', '--id', 'x', '--query', 'q'],
        ['label'],
        ['label', '--count', '0'],
        ['label', '--count', '1001'],
        ['label', '--count', 'abc'],
    ],
)
def test_label_selector_errors(argv):
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    assert e.value.code == 2


def test_label_count_alone_uses_inbox(label_env, capsys):
    assert cli.main(['label', '--count', '5']) == 0
    label_env.list_message_ids.assert_called_once_with(query='in:inbox', max_results=5)


def test_label_query_alone_caps_at_100(label_env):
    cli.main(['label', '--query', 'from:x'])
    label_env.list_message_ids.assert_called_once_with(query='from:x', max_results=100)


def test_label_query_with_count(label_env):
    cli.main(['label', '--query', 'from:x', '--count', '7'])
    label_env.list_message_ids.assert_called_once_with(query='from:x', max_results=7)


def test_label_ids_skip_search(label_env, pipeline):
    cli.main(['label', '--id', 'x', '--id', 'y'])
    label_env.list_message_ids.assert_not_called()
    assert list(pipeline.process_many.call_args.args[0]) == ['x', 'y']


def test_label_dry_run_by_default(label_env, pipeline):
    cli.main(['label', '--count', '1'])
    assert pipeline.process_many.call_args.kwargs == {'apply': False, 'force': False}


def test_label_apply_and_force(label_env, pipeline):
    cli.main(['label', '--count', '1', '--apply', '--force'])
    assert pipeline.process_many.call_args.kwargs == {'apply': True, 'force': True}


def test_label_json_to_stdout_and_summary(label_env, capsys):
    cli.main(['label', '--count', '2'])
    out, err = capsys.readouterr()
    assert [r['message_id'] for r in json.loads(out)] == ['a', 'b']
    assert err.strip() == (
        'Processed 2 emails: 0 labelled, 2 would label, 0 no label, 0 skipped, 0 errors'
    )


@pytest.mark.parametrize(
    ('output', 'extra', 'fmt'),
    [
        ('r.csv', [], 'csv'),
        ('r.CSV', [], 'csv'),
        ('r.json', [], 'json'),
        ('r.txt', [], 'json'),
        ('r.json', ['--format', 'csv'], 'csv'),
        ('r.csv', ['--format', 'json'], 'json'),
    ],
)
def test_label_output_file_formats(label_env, capsys, output, extra, fmt):
    cli.main(['label', '--count', '2', '--output', output, *extra])
    out, err = capsys.readouterr()
    assert out == ''
    assert cli.write_results.call_args.args[1:] == (Path(output), fmt)
    assert err.strip().endswith(f'-> {output}')


def test_label_exit_4_on_item_error(label_env, pipeline):
    pipeline.process_many.side_effect = lambda ids, apply, force: iter(
        [make_result('a', PipelineStatus.ERROR)]
    )
    assert cli.main(['label', '--count', '1']) == 4


# --- listen


def test_listen_runs_service(monkeypatch):
    service = MagicMock()
    build = MagicMock(return_value=service)
    monkeypatch.setattr(cli, 'build_service', build)
    assert cli.main(['listen', '--dry-run']) == 0
    assert build.call_args.kwargs['dry_run'] is True
    service.run.assert_called_once_with()


def test_listen_flags_reach_config(monkeypatch):
    build = MagicMock()
    monkeypatch.setattr(cli, 'build_service', build)
    cli.main(
        [
            'listen',
            '--topic',
            'projects/p/topics/t',
            '--subscription',
            'projects/p/subscriptions/s',
            '--jev-model',
            'jev-1',
            '--max-body-chars',
            '500',
        ]
    )
    config = build.call_args.args[0]
    assert config.pubsub_topic == 'projects/p/topics/t'
    assert config.pubsub_subscription == 'projects/p/subscriptions/s'
    assert (config.jev_model, config.max_body_chars) == ('jev-1', 500)


def test_listen_missing_topic_exits_2():
    assert cli.main(['listen']) == 2


# --- watch


@pytest.fixture
def state(monkeypatch, memory_state):
    monkeypatch.setattr(cli, 'StateStore', MagicMock(return_value=memory_state))
    memory_state.close = MagicMock(wraps=memory_state.close)
    return memory_state


def test_watch_start(monkeypatch, state, capsys):
    gmail = MagicMock()
    monkeypatch.setattr(cli, 'build_gmail', MagicMock(return_value=gmail))
    renew = MagicMock(
        side_effect=lambda *a, **k: state.set('watch_expiration_ms', '1700000000000')
    )
    monkeypatch.setattr(cli, 'renew_watch', renew)
    assert cli.main(['watch', 'start', '--topic', 'projects/p/topics/t']) == 0
    assert renew.call_args.args[2] == 'projects/p/topics/t'
    assert renew.call_args.kwargs['force'] is True
    status = json.loads(capsys.readouterr().out)
    assert status['watch_expiration'] == '2023-11-14T22:13:20+00:00'
    state.close.assert_called_once()


def test_watch_start_requires_topic(state):
    assert cli.main(['watch', 'start']) == 2


def test_watch_stop(monkeypatch, state):
    gmail = MagicMock()
    monkeypatch.setattr(cli, 'build_gmail', MagicMock(return_value=gmail))
    state.set('watch_expiration_ms', '1')
    state.set('watch_renewed_at', '1')
    state.clear_watch = MagicMock(wraps=state.clear_watch)
    assert cli.main(['watch', 'stop']) == 0
    gmail.stop_watch.assert_called_once()
    state.clear_watch.assert_called_once_with()


def test_watch_status(state, capsys):
    state.advance_cursor(42)
    state.set('watch_renewed_at', '1700000000')
    state.set('last_sync_at', '1700000060')
    assert cli.main(['watch', 'status']) == 0
    assert json.loads(capsys.readouterr().out) == {
        'history_cursor': 42,
        'watch_expiration': None,
        'watch_renewed_at': '2023-11-14T22:13:20+00:00',
        'last_sync_at': '2023-11-14T22:14:20+00:00',
    }


def test_watch_status_empty(state, capsys):
    cli.main(['watch', 'status'])
    assert set(json.loads(capsys.readouterr().out).values()) == {None}


# --- criteria / config


def test_criteria_example_is_valid(capsys):
    assert cli.main(['criteria', 'example']) == 0
    parse_criteria(json.loads(capsys.readouterr().out))
    assert json.loads(json.dumps(EXAMPLE_CRITERIA))


def test_criteria_validate(monkeypatch, criteria, capsys):
    load = MagicMock(return_value=criteria)
    monkeypatch.setattr(cli, 'load_criteria', load)
    assert cli.main(['criteria', 'validate', '--criteria-file', '/c.json']) == 0
    assert load.call_args.args[0] == Path('/c.json')
    assert capsys.readouterr().out.splitlines() == [
        'OK: 3 categories',
        'receipt -> Jev/receipt',
        'news -> Jev/Reading',
        'personal -> (no label)',
    ]


def test_criteria_validate_error_exits_2(monkeypatch, caplog):
    monkeypatch.setattr(
        cli,
        'load_criteria',
        MagicMock(side_effect=CriteriaError(['version: required'], 'c')),
    )
    assert cli.main(['criteria', 'validate']) == 2
    assert 'version: required' in caplog.text


def test_config_show_redacts_key(monkeypatch, capsys):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'super-secret-key')
    assert cli.main(['config', 'show', '--config-json', '{"jev_model": "m"}']) == 0
    out = capsys.readouterr().out
    assert 'super-secret-key' not in out
    data = json.loads(out)
    assert data['typesafe_api_key'] == 'set'
    assert data['jev_model'] == 'm'


def test_config_error_exits_2(caplog):
    assert cli.main(['config', 'show', '--config-json', '{"nope": 1}']) == 2
    assert 'unknown key' in caplog.text


# --- exit codes


@pytest.mark.parametrize(
    ('exc', 'code', 'text'),
    [
        (ConfigError('bad config'), 2, 'bad config'),
        (
            AuthError('No token at x'),
            3,
            'Authorization failed: No token at x. Run `jev-gmail-labeler auth`',
        ),
        (AuthError('TypeSafe API key was rejected'), 3, 'TypeSafe API key was rejected'),
        (KeyboardInterrupt(), 130, ''),
        (GmailError('boom'), 1, 'Unexpected error'),
        (RuntimeError('kaboom'), 1, 'Unexpected error'),
    ],
)
def test_exit_codes(monkeypatch, caplog, exc, code, text):
    monkeypatch.setattr(cli, 'run_consent_flow', MagicMock(side_effect=exc))
    assert cli.main(['auth']) == code
    assert text in caplog.text
    if code == 3 and text.startswith('TypeSafe'):
        assert 'Authorization failed' not in caplog.text


def test_logging_configured_twice(monkeypatch):
    cli.main(['config', 'show', '--log-level', 'DEBUG'])
    levels = [c.args[0] for c in cli.configure_logging.call_args_list]
    assert levels == ['INFO', 'DEBUG']
