import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

from jev_gmail_labeler import state as state_module
from jev_gmail_labeler.state import MessageRecord, StateStore


def test_schema_created(memory_state):
    names = {
        r[0]
        for r in memory_state._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {'kv', 'messages'} <= names


def test_kv_get_set_delete(memory_state):
    assert memory_state.get('a') is None
    memory_state.set('a', '1')
    memory_state.set('a', '2')
    assert memory_state.get('a') == '2'
    memory_state.delete('a')
    assert memory_state.get('a') is None


def test_cursor_is_monotonic(memory_state):
    assert memory_state.get_cursor() is None
    memory_state.advance_cursor(10)
    memory_state.advance_cursor(5)
    assert memory_state.get_cursor() == 10
    memory_state.advance_cursor(11)
    assert memory_state.get_cursor() == 11


def test_message_unknown(memory_state):
    assert memory_state.message('x') is None


def test_mark_done(memory_state):
    memory_state.mark_done('m1', result_status='labelled', category='c', label_name='L')
    assert memory_state.message('m1') == MessageRecord('m1', 'done', 0, None, None)


def test_mark_retry_and_failed(memory_state):
    memory_state.mark_retry('m1', attempts=2, error='boom', next_retry_at=50.0)
    assert memory_state.message('m1') == MessageRecord('m1', 'retry', 2, 50.0, 'boom')
    memory_state.mark_failed('m1', attempts=3, error='dead')
    assert memory_state.message('m1') == MessageRecord('m1', 'failed', 3, None, 'dead')


def test_due_retries_oldest_first(memory_state):
    memory_state.mark_retry('b', attempts=1, error='e', next_retry_at=20.0)
    memory_state.mark_retry('a', attempts=1, error='e', next_retry_at=30.0)
    memory_state.mark_retry('c', attempts=1, error='e', next_retry_at=10.0)
    memory_state.mark_retry('late', attempts=1, error='e', next_retry_at=99.0)
    memory_state.mark_done('d', result_status='labelled', category=None, label_name=None)
    assert memory_state.due_retries(30.0) == ['c', 'b', 'a']


def test_prune(memory_state, fake_clock):
    memory_state.mark_done(
        'old', result_status='labelled', category=None, label_name=None
    )
    memory_state.mark_failed('oldfail', attempts=1, error='e')
    memory_state.mark_retry('pending', attempts=1, error='e', next_retry_at=1.0)
    fake_clock.advance(100)
    memory_state.mark_done(
        'new', result_status='labelled', category=None, label_name=None
    )
    assert memory_state.prune(fake_clock.time() - 50) == 2
    assert memory_state.message('old') is None
    assert memory_state.message('pending') is not None
    assert memory_state.message('new') is not None


def test_clear_watch(memory_state):
    memory_state.set('watch_expiration_ms', '1')
    memory_state.set('watch_renewed_at', '2')
    memory_state.set('history_cursor', '3')
    memory_state.clear_watch()
    assert memory_state.get('watch_expiration_ms') is None
    assert memory_state.get('watch_renewed_at') is None
    assert memory_state.get('history_cursor') == '3'


def test_close(memory_state):
    memory_state.close()
    try:
        memory_state.get('a')
    except sqlite3.ProgrammingError:
        return
    raise AssertionError('expected closed database')


def test_file_path_creates_parent_dir(monkeypatch):
    ensure_dir = MagicMock()
    connect = MagicMock(return_value=sqlite3.connect(':memory:'))
    monkeypatch.setattr(state_module.files, 'ensure_dir', ensure_dir)
    monkeypatch.setattr(state_module.sqlite3, 'connect', connect)
    StateStore(Path('/fake/state/state.sqlite3'))
    ensure_dir.assert_called_once_with(Path('/fake/state'))
    connect.assert_called_once_with('/fake/state/state.sqlite3')
