import signal
import threading
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from jev_gmail_labeler import service as service_module
from jev_gmail_labeler.errors import (
    AuthError,
    GmailError,
    HistoryExpiredError,
    PubSubError,
)
from jev_gmail_labeler.google_api.gmail import HistoryPage, Profile, WatchResponse
from jev_gmail_labeler.google_api.pubsub import PubSubPuller, PulledMessage
from jev_gmail_labeler.models import (
    DecisionReason,
    LabelDecision,
    PipelineResult,
    PipelineStatus,
)
from jev_gmail_labeler.pipeline import Pipeline
from jev_gmail_labeler.service import (
    PRUNE_AFTER_SECONDS,
    LabelerService,
    renew_watch,
)

TOPIC = 'projects/p/topics/t'


def make_result(mid='m1', status=PipelineStatus.LABELLED, *, retryable=False, error=None):
    decision = LabelDecision(
        message_id=mid,
        category='receipt',
        reason=DecisionReason.MATCHED,
        label_name='Jev/R',
    )
    return PipelineResult(
        message_id=mid,
        thread_id=None,
        date=None,
        sender=None,
        subject=None,
        status=status,
        skip_reason=None,
        classification=None,
        decision=None if status is PipelineStatus.ERROR else decision,
        applied=False,
        label_id=None,
        error=error,
        retryable=retryable,
        processed_at='2026-10-03T12:00:00+00:00',
    )


@pytest.fixture
def puller():
    mock = MagicMock(spec=PubSubPuller)
    mock.pull.return_value = []
    return mock


@pytest.fixture
def pipeline():
    mock = MagicMock(spec=Pipeline)
    mock.process.side_effect = lambda mid, apply, force=False: make_result(mid)
    return mock


@pytest.fixture
def stop_event():
    return threading.Event()


@pytest.fixture
def svc(app_config, gmail_client, puller, pipeline, memory_state, fake_clock, stop_event):
    gmail_client.get_profile.return_value = Profile('me@example.com', 500)
    gmail_client.watch.return_value = WatchResponse(history_id=500, expiration_ms=10**13)
    gmail_client.list_history.return_value = HistoryPage((), 600)
    return LabelerService(
        config=app_config,
        gmail=gmail_client,
        puller=puller,
        pipeline=pipeline,
        state=memory_state,
        stop_event=stop_event,
        clock=fake_clock.time,
        monotonic=fake_clock.monotonic,
    )


# --- renew_watch


def test_renew_watch_force(gmail_client, memory_state, fake_clock):
    gmail_client.watch.return_value = WatchResponse(
        500, int((fake_clock.now + 7 * 86400) * 1000)
    )
    memory_state.set('watch_expiration_ms', str(int((fake_clock.now + 7 * 86400) * 1000)))
    memory_state.set('watch_renewed_at', str(fake_clock.now))
    assert renew_watch(gmail_client, memory_state, TOPIC, now=fake_clock.now, force=True)
    gmail_client.watch.assert_called_once_with(TOPIC)


def test_renew_watch_first_time_seeds_cursor(gmail_client, memory_state, fake_clock):
    gmail_client.watch.return_value = WatchResponse(
        500, int((fake_clock.now + 7 * 86400) * 1000)
    )
    assert renew_watch(gmail_client, memory_state, TOPIC, now=fake_clock.now)
    assert memory_state.get_cursor() == 500
    assert memory_state.get('watch_renewed_at') == str(fake_clock.now)


def test_renew_watch_keeps_existing_cursor(gmail_client, memory_state, fake_clock):
    memory_state.advance_cursor(100)
    gmail_client.watch.return_value = WatchResponse(
        500, int((fake_clock.now + 7 * 86400) * 1000)
    )
    renew_watch(gmail_client, memory_state, TOPIC, now=fake_clock.now)
    assert memory_state.get_cursor() == 100


def _fresh_watch(memory_state, now, *, renewed_ago=60, expires_in=7 * 86400):
    memory_state.set('watch_renewed_at', str(now - renewed_ago))
    memory_state.set('watch_expiration_ms', str(int((now + expires_in) * 1000)))


def test_renew_watch_not_due(gmail_client, memory_state, fake_clock):
    _fresh_watch(memory_state, fake_clock.now)
    assert not renew_watch(gmail_client, memory_state, TOPIC, now=fake_clock.now)
    gmail_client.watch.assert_not_called()


def test_renew_watch_due_by_age(gmail_client, memory_state, fake_clock):
    _fresh_watch(memory_state, fake_clock.now, renewed_ago=86400)
    gmail_client.watch.return_value = WatchResponse(5, 10**13)
    assert renew_watch(gmail_client, memory_state, TOPIC, now=fake_clock.now)


def test_renew_watch_due_by_expiry(gmail_client, memory_state, fake_clock):
    _fresh_watch(memory_state, fake_clock.now, expires_in=3600)
    gmail_client.watch.return_value = WatchResponse(5, 10**13)
    assert renew_watch(gmail_client, memory_state, TOPIC, now=fake_clock.now)


# --- sync / catch_up / process


def test_sync_seeds_cursor_when_missing(svc, gmail_client, memory_state):
    svc.sync()
    assert memory_state.get_cursor() == 500
    gmail_client.list_history.assert_not_called()


def test_sync_processes_then_advances(
    svc, gmail_client, memory_state, pipeline, fake_clock
):
    memory_state.advance_cursor(100)
    gmail_client.list_history.return_value = HistoryPage(('a', 'b'), 700)
    svc.sync()
    assert [c.args[0] for c in pipeline.process.call_args_list] == ['a', 'b']
    assert memory_state.get_cursor() == 700
    assert memory_state.get('last_sync_at') == str(fake_clock.now)


def test_sync_does_not_advance_when_processing_raises(
    svc, gmail_client, memory_state, pipeline
):
    memory_state.advance_cursor(100)
    gmail_client.list_history.return_value = HistoryPage(('a',), 700)
    pipeline.process.side_effect = AuthError('revoked')
    with pytest.raises(AuthError):
        svc.sync()
    assert memory_state.get_cursor() == 100


def test_sync_history_expired_runs_catch_up(svc, gmail_client, memory_state):
    memory_state.advance_cursor(100)
    gmail_client.list_history.side_effect = HistoryExpiredError('gone', status=404)
    gmail_client.list_message_ids.return_value = []
    svc.sync()
    assert memory_state.get_cursor() == 500


def test_catch_up_query_order_and_baseline(
    svc, gmail_client, memory_state, pipeline, fake_clock
):
    memory_state.set('last_sync_at', str(fake_clock.now - 7200))
    gmail_client.list_message_ids.return_value = ['new', 'old']  # newest first
    svc.catch_up()
    gmail_client.list_message_ids.assert_called_once_with(
        query=f'in:inbox after:{int(fake_clock.now - 7200 - 3600)}', max_results=200
    )
    assert [c.args[0] for c in pipeline.process.call_args_list] == ['old', 'new']
    assert memory_state.get_cursor() == 500
    assert memory_state.get('last_sync_at') == str(fake_clock.now)


def test_catch_up_without_last_sync_uses_one_day(svc, gmail_client, fake_clock):
    gmail_client.list_message_ids.return_value = []
    svc.catch_up()
    since = int(fake_clock.now - 86400 - 3600)
    assert (
        gmail_client.list_message_ids.call_args.kwargs['query']
        == f'in:inbox after:{since}'
    )


def test_process_done_is_skipped(svc, pipeline, memory_state):
    memory_state.mark_done('m1', result_status='labelled', category=None, label_name=None)
    svc.process('m1')
    pipeline.process.assert_not_called()


def test_process_failed_is_skipped(svc, pipeline, memory_state):
    memory_state.mark_failed('m1', attempts=3, error='x')
    svc.process('m1')
    pipeline.process.assert_not_called()


def test_process_retry_not_due_is_skipped(svc, pipeline, memory_state, fake_clock):
    memory_state.mark_retry(
        'm1', attempts=1, error='x', next_retry_at=fake_clock.now + 10
    )
    svc.process('m1')
    pipeline.process.assert_not_called()


def test_process_success_marks_done(svc, pipeline, memory_state):
    svc.process('m1')
    pipeline.process.assert_called_once_with('m1', apply=True)
    assert memory_state.message('m1').status == 'done'


def test_process_no_decision_marks_done(svc, pipeline, memory_state):
    skipped = make_result('m1', PipelineStatus.SKIPPED)
    pipeline.process.side_effect = None
    pipeline.process.return_value = skipped.__class__.from_dict(
        {**skipped.to_dict(), 'decision': None}
    )
    svc.process('m1')
    assert memory_state.message('m1').status == 'done'


@pytest.mark.parametrize(('attempt', 'delay'), [(1, 60), (2, 120)])
def test_process_error_schedules_backoff(
    svc, pipeline, memory_state, fake_clock, attempt, delay
):
    pipeline.process.side_effect = None
    pipeline.process.return_value = make_result(
        'm1', PipelineStatus.ERROR, retryable=True, error='fetch: boom'
    )
    for n in range(1, attempt + 1):
        if n > 1:
            fake_clock.advance(10_000)
        svc.process('m1')
    rec = memory_state.message('m1')
    assert (rec.status, rec.attempts) == ('retry', attempt)
    assert rec.next_retry_at == fake_clock.now + delay


def test_process_backoff_is_capped(svc, pipeline, memory_state, fake_clock, app_config):
    memory_state.mark_retry('m1', attempts=9, error='x', next_retry_at=0.0)
    pipeline.process.side_effect = None
    pipeline.process.return_value = make_result(
        'm1', PipelineStatus.ERROR, retryable=True, error='e'
    )
    svc._config = replace(app_config, max_attempts=50)
    svc.process('m1')
    assert memory_state.message('m1').next_retry_at == fake_clock.now + 3600


def test_process_max_attempts_marks_failed(svc, pipeline, memory_state):
    memory_state.mark_retry('m1', attempts=2, error='x', next_retry_at=0.0)
    pipeline.process.side_effect = None
    pipeline.process.return_value = make_result(
        'm1', PipelineStatus.ERROR, retryable=True, error='e'
    )
    svc.process('m1')
    assert memory_state.message('m1').status == 'failed'


def test_process_non_retryable_marks_failed(svc, pipeline, memory_state):
    pipeline.process.side_effect = None
    pipeline.process.return_value = make_result('m1', PipelineStatus.ERROR, error=None)
    svc.process('m1')
    rec = memory_state.message('m1')
    assert (rec.status, rec.attempts, rec.last_error) == ('failed', 1, 'unknown error')


def test_process_dry_run_writes_no_state(
    app_config, gmail_client, puller, pipeline, memory_state
):
    dry = LabelerService(
        config=app_config,
        gmail=gmail_client,
        puller=puller,
        pipeline=pipeline,
        state=memory_state,
        dry_run=True,
    )
    dry.process('m1')
    pipeline.process.assert_called_once_with('m1', apply=False)
    assert memory_state.message('m1') is None


def test_retry_due_processes_due_ids(svc, pipeline, memory_state, fake_clock):
    memory_state.mark_retry('m1', attempts=1, error='x', next_retry_at=fake_clock.now - 1)
    memory_state.mark_retry(
        'm2', attempts=1, error='x', next_retry_at=fake_clock.now + 100
    )
    svc.retry_due()
    assert [c.args[0] for c in pipeline.process.call_args_list] == ['m1']


# --- handle_batch


def test_handle_batch_stale_acks_without_sync(
    svc, puller, gmail_client, memory_state, make_pulled
):
    svc.profile = Profile('me@example.com', 1)
    memory_state.advance_cursor(100)
    svc.handle_batch([make_pulled(100, ack_id='a'), make_pulled(50, ack_id='b')])
    gmail_client.list_history.assert_not_called()
    puller.ack.assert_called_once_with(['a', 'b'])


def test_handle_batch_new_syncs_then_acks(
    svc, puller, gmail_client, memory_state, make_pulled
):
    svc.profile = Profile('ME@example.com', 1)
    memory_state.advance_cursor(100)
    svc.handle_batch([make_pulled(200)])
    gmail_client.list_history.assert_called_once_with(100)
    puller.ack.assert_called_once_with(['a1'])


def test_handle_batch_loads_profile_when_missing(svc, puller, gmail_client, make_pulled):
    svc.handle_batch([make_pulled(5)])
    gmail_client.get_profile.assert_called()
    puller.ack.assert_called_once()


def test_handle_batch_foreign_mailbox_ignored(
    svc, puller, gmail_client, memory_state, make_pulled
):
    svc.profile = Profile('me@example.com', 1)
    memory_state.advance_cursor(100)
    svc.handle_batch([make_pulled(900, email='other@example.com')])
    gmail_client.list_history.assert_not_called()
    puller.ack.assert_called_once_with(['a1'])


def test_handle_batch_invalid_data_acked(svc, puller, gmail_client):
    svc.profile = Profile('me@example.com', 1)
    svc.handle_batch([PulledMessage('x', b'junk', 'pm')])
    gmail_client.list_history.assert_not_called()
    puller.ack.assert_called_once_with(['x'])


def test_handle_batch_transient_error_nacks(
    svc, puller, gmail_client, memory_state, make_pulled, stop_event
):
    svc.profile = Profile('me@example.com', 1)
    memory_state.advance_cursor(100)
    gmail_client.list_history.side_effect = GmailError('boom', transient=True)
    stop_event.wait = MagicMock()
    svc.handle_batch([make_pulled(200)])
    puller.nack.assert_called_once_with(['a1'])
    puller.ack.assert_not_called()
    stop_event.wait.assert_called_once_with(5)


def test_handle_batch_permanent_gmail_error_propagates(
    svc, gmail_client, memory_state, make_pulled
):
    svc.profile = Profile('me@example.com', 1)
    memory_state.advance_cursor(100)
    gmail_client.list_history.side_effect = GmailError('bad', transient=False)
    with pytest.raises(GmailError):
        svc.handle_batch([make_pulled(200)])


def test_handle_batch_seeds_when_no_cursor(
    svc, puller, gmail_client, memory_state, make_pulled
):
    svc.profile = Profile('me@example.com', 1)
    svc.handle_batch([make_pulled(1)])
    assert memory_state.get_cursor() == 500
    puller.ack.assert_called_once()


# --- run


@pytest.fixture
def signals(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(service_module.signal, 'signal', mock)
    return mock


def test_run_installs_signals_and_closes_resources(
    svc, puller, pipeline, memory_state, signals, stop_event
):
    stop_event.set()
    svc.run()
    signals.assert_any_call(signal.SIGINT, svc.request_stop)
    signals.assert_any_call(signal.SIGTERM, svc.request_stop)
    puller.close.assert_called_once()
    pipeline.close.assert_called_once()
    with pytest.raises(Exception, match='closed'):
        memory_state.get('x')


def test_run_startup_sequence(
    svc, gmail_client, memory_state, stop_event, signals, fake_clock
):
    memory_state.mark_done('old', result_status='x', category=None, label_name=None)
    fake_clock.advance(PRUNE_AFTER_SECONDS + 1)
    stop_event.set()
    svc.run()
    gmail_client.watch.assert_called_once_with(TOPIC)
    assert svc.profile.email_address == 'me@example.com'


def test_run_handles_batches_and_stops(
    svc, puller, gmail_client, memory_state, make_pulled, stop_event, signals
):
    def pull(*a, **k):
        stop_event.set()
        return [make_pulled(900)]

    puller.pull.side_effect = pull
    svc.run()
    puller.ack.assert_called_once_with(['a1'])
    puller.pull.assert_called_once_with(10, 20.0)


def test_run_idle_resync(
    svc, puller, gmail_client, memory_state, fake_clock, stop_event, signals
):
    calls = []

    def pull(*a, **k):
        calls.append(1)
        fake_clock.advance(15 * 60)
        if len(calls) == 2:
            stop_event.set()
        return []

    puller.pull.side_effect = pull
    svc.run()
    # one sync at startup plus one per idle period
    assert gmail_client.list_history.call_count == 3


def test_run_pubsub_backoff(svc, puller, stop_event, signals):
    waits = []
    stop_event.wait = lambda delay: waits.append(delay)
    errors = [PubSubError('x', transient=True)] * 3

    def pull(*a, **k):
        if errors:
            raise errors.pop()
        stop_event.set()
        return []

    puller.pull.side_effect = pull
    svc.run()
    assert waits == [2, 4, 8]


def test_run_backoff_resets_after_success(svc, puller, stop_event, signals):
    waits = []
    stop_event.wait = lambda delay: waits.append(delay)
    seq = iter([PubSubError('x', transient=True), [], PubSubError('x', transient=True)])

    def pull(*a, **k):
        try:
            item = next(seq)
        except StopIteration:
            stop_event.set()
            return []
        if isinstance(item, Exception):
            raise item
        return item

    puller.pull.side_effect = pull
    svc.run()
    assert waits == [2, 2]


def test_run_backoff_capped_at_sixty(svc, puller, stop_event, signals):
    waits = []
    stop_event.wait = lambda delay: waits.append(delay)
    count = {'n': 0}

    def pull(*a, **k):
        count['n'] += 1
        if count['n'] > 7:
            stop_event.set()
            return []
        raise PubSubError('x', transient=True)

    puller.pull.side_effect = pull
    svc.run()
    assert max(waits) == 60


def test_run_permanent_pubsub_error_propagates(svc, puller, pipeline, signals):
    puller.pull.side_effect = PubSubError('bad', transient=False)
    with pytest.raises(PubSubError):
        svc.run()
    puller.close.assert_called_once()


def test_run_auth_error_propagates(svc, puller, gmail_client, signals):
    gmail_client.get_profile.side_effect = AuthError('revoked')
    with pytest.raises(AuthError):
        svc.run()
    puller.close.assert_called_once()


def test_request_stop_sets_event(svc, stop_event):
    svc.request_stop(signal.SIGTERM, None)
    assert stop_event.is_set()


def test_default_stop_event(app_config, gmail_client, puller, pipeline, memory_state):
    s = LabelerService(
        config=app_config,
        gmail=gmail_client,
        puller=puller,
        pipeline=pipeline,
        state=memory_state,
    )
    s.request_stop()
    assert s._stop.is_set()
