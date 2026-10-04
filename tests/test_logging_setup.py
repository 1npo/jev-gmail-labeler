import io
import logging

import pytest

from jev_gmail_labeler import logging_setup as ls


@pytest.fixture(autouse=True)
def restore_logging(monkeypatch):
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    monkeypatch.setattr(ls, '_secrets', set())
    names = ('presidio-analyzer', 'presidio-anonymizer', 'typesafe_sdk')
    saved = {n: logging.getLogger(n).level for n in names}
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    for n, lvl in saved.items():
        logging.getLogger(n).setLevel(lvl)


def log_once(environ, message='hello', *args, level='INFO'):
    stream = io.StringIO()
    ls.configure_logging(level, environ=environ, stream=stream)
    logging.getLogger('x.y').info(message, *args)
    return stream.getvalue()


def test_normal_format_has_timestamp():
    out = log_once({})
    assert out.endswith(' INFO x.y: hello\n')
    assert out[0].isdigit()


def test_journald_format_has_no_timestamp():
    assert log_once({'JOURNAL_STREAM': '8:1'}) == 'INFO x.y: hello\n'


def test_level_applied():
    stream = io.StringIO()
    ls.configure_logging('WARNING', environ={}, stream=stream)
    logging.getLogger('x').info('quiet')
    logging.getLogger('x').warning('loud')
    assert 'quiet' not in stream.getvalue()
    assert 'loud' in stream.getvalue()


def test_configure_is_idempotent():
    ls.configure_logging('INFO', environ={}, stream=io.StringIO())
    ls.configure_logging('INFO', environ={}, stream=io.StringIO())
    assert len(logging.getLogger().handlers) == 1


def test_secret_redacted_in_msg_and_args():
    ls.register_secret('super-secret-token')
    out = log_once({}, 'token=super-secret-token')
    assert 'super-secret-token' not in out
    assert 'token=***' in out
    out = log_once({}, 'token=%s', 'super-secret-token')
    assert 'super-secret-token' not in out
    assert 'token=***' in out


def test_longest_secret_wins():
    ls.register_secret('abcdefgh')
    ls.register_secret('abcdefghijkl')
    assert 'v=***' in log_once({}, 'v=abcdefghijkl')


@pytest.mark.parametrize('value', [None, '', 'short'])
def test_short_or_empty_secrets_ignored(value):
    ls.register_secret(value)
    assert ls._secrets == set()
    assert 'short' in log_once({}, 'short')


def test_noisy_loggers_quieted():
    ls.configure_logging('DEBUG', environ={}, stream=io.StringIO())
    assert logging.getLogger('presidio-analyzer').level == logging.ERROR
    assert logging.getLogger('presidio-anonymizer').level == logging.ERROR
    assert logging.getLogger('googleapiclient.discovery_cache').level == logging.ERROR
    assert logging.getLogger('typesafe_sdk').level == logging.WARNING
