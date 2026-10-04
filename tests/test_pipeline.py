import logging
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from jev_gmail_labeler.errors import (
    AuthError,
    ClassificationError,
    ConfigError,
    GmailError,
    MessageNotFoundError,
)
from jev_gmail_labeler.models import (
    DecisionReason,
    PipelineStatus,
    SkipReason,
)
from jev_gmail_labeler.pipeline import Pipeline, decide
from jev_gmail_labeler.typesafe_api.classifier import JevClassifier

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def classifier(make_classification):
    mock = MagicMock(spec=JevClassifier)
    mock.classify.return_value = make_classification()
    return mock


@pytest.fixture
def pipeline(gmail_client, label_manager, anonymizer, classifier, criteria):
    return Pipeline(
        gmail=gmail_client,
        labels=label_manager,
        anonymizer=anonymizer,
        classifier=classifier,
        criteria=criteria,
        max_body_chars=1234,
        now=lambda: NOW,
    )


# --- decide ---------------------------------------------------------------


def test_decide_matched(make_classification, criteria):
    d = decide(make_classification('news', 0.9), criteria)
    assert (d.reason, d.label_name, d.category) == (
        DecisionReason.MATCHED,
        'Jev/Reading',
        'news',
    )
    assert d.message_id == 'm1'


def test_decide_matched_with_null_label(make_classification, criteria):
    d = decide(make_classification('personal', 0.9), criteria)
    assert (d.reason, d.label_name) == (DecisionReason.MATCHED, None)


def test_decide_no_match_uses_no_match_label(make_classification, criteria_data):
    from jev_gmail_labeler.criteria import parse_criteria

    criteria_data['no_match_label'] = 'Other'
    c = parse_criteria(criteria_data)
    d = decide(make_classification('_none', 0.99), c)
    assert (d.reason, d.label_name) == (DecisionReason.NO_MATCH, 'Jev/Other')


def test_decide_no_match_without_label(make_classification, criteria):
    d = decide(make_classification('_none', 0.99), criteria)
    assert (d.reason, d.label_name) == (DecisionReason.NO_MATCH, None)


def test_decide_low_confidence(make_classification, criteria):
    d = decide(make_classification('receipt', 0.3), criteria)
    assert (d.reason, d.label_name) == (DecisionReason.LOW_CONFIDENCE, 'Jev/Unsure')


def test_decide_exactly_min_confidence_matches(make_classification, criteria):
    assert decide(make_classification('receipt', 0.5), criteria).reason == (
        DecisionReason.MATCHED
    )


def test_decide_no_match_beats_low_confidence(make_classification, criteria):
    assert decide(make_classification('_none', 0.1), criteria).reason == (
        DecisionReason.NO_MATCH
    )


def test_decide_unknown_category_has_no_label(make_classification, criteria):
    d = decide(make_classification('ghost', 0.9), criteria)
    assert (d.reason, d.label_name) == (DecisionReason.MATCHED, None)


# --- process --------------------------------------------------------------


def test_not_found_is_skipped(pipeline, gmail_client, classifier):
    gmail_client.get_message.side_effect = MessageNotFoundError('gone', status=404)
    r = pipeline.process('m9', apply=True)
    assert r.status is PipelineStatus.SKIPPED
    assert r.skip_reason is SkipReason.NOT_FOUND
    assert r.message_id == 'm9'
    assert r.thread_id is None
    classifier.classify.assert_not_called()


def test_already_labelled_is_skipped(
    pipeline, label_manager, classifier, anonymizer, email_message
):
    label_manager.existing_ids.return_value = {'UNREAD'}  # carried by the fixture message
    r = pipeline.process('m1', apply=True)
    assert r.status is PipelineStatus.SKIPPED
    assert r.skip_reason is SkipReason.ALREADY_LABELLED
    assert r.thread_id == 't1'
    assert r.subject == 'Your receipt'
    anonymizer.anonymize.assert_not_called()
    classifier.classify.assert_not_called()


def test_skip_check_queries_managed_labels(pipeline, label_manager, criteria):
    pipeline.process('m1', apply=False)
    label_manager.existing_ids.assert_called_once_with(criteria.managed_label_names())


def test_force_bypasses_skip(pipeline, label_manager, classifier):
    label_manager.existing_ids.return_value = {'UNREAD'}
    r = pipeline.process('m1', apply=False, force=True)
    assert r.status is PipelineStatus.WOULD_LABEL
    classifier.classify.assert_called_once()


def test_dry_run_would_label_and_never_creates(pipeline, label_manager, gmail_client):
    label_manager.find_id.return_value = 'L-existing'
    r = pipeline.process('m1', apply=False)
    assert r.status is PipelineStatus.WOULD_LABEL
    assert r.applied is False
    assert r.label_id == 'L-existing'
    assert r.decision.label_name == 'Jev/Receipt'
    assert r.classification.category == 'receipt'
    label_manager.find_id.assert_called_once_with('Jev/Receipt')
    label_manager.ensure.assert_not_called()
    gmail_client.modify_labels.assert_not_called()


def test_dry_run_label_missing_has_no_id(pipeline):
    r = pipeline.process('m1', apply=False)
    assert r.status is PipelineStatus.WOULD_LABEL
    assert r.label_id is None


def test_apply_ensures_and_modifies(pipeline, label_manager, gmail_client, anonymizer):
    r = pipeline.process('m1', apply=True)
    assert r.status is PipelineStatus.LABELLED
    assert r.applied is True
    assert r.label_id == 'L-new'
    assert r.error is None
    assert r.processed_at == '2026-10-03T12:00:00+00:00'
    assert (r.thread_id, r.date, r.sender) == (
        't1',
        'Tue, 3 Oct 2026 12:00:00 +0000',
        'Alice <alice@example.com>',
    )
    label_manager.ensure.assert_called_once_with('Jev/Receipt')
    gmail_client.modify_labels.assert_called_once_with('m1', add=['L-new'], remove=[])
    assert anonymizer.anonymize.call_args.kwargs == {'max_body_chars': 1234}


def test_force_apply_removes_other_managed_labels(
    pipeline, label_manager, gmail_client, email_message
):
    from dataclasses import replace

    gmail_client.get_message.return_value = replace(
        email_message, label_ids=('INBOX', 'L-old', 'L-new', 'L-other')
    )
    label_manager.existing_ids.return_value = {'L-old', 'L-new', 'L-other'}
    r = pipeline.process('m1', apply=True, force=True)
    assert r.status is PipelineStatus.LABELLED
    gmail_client.modify_labels.assert_called_once_with(
        'm1', add=['L-new'], remove=['L-old', 'L-other']
    )


def test_apply_without_force_does_not_remove(pipeline, gmail_client):
    pipeline.process('m1', apply=True)
    assert gmail_client.modify_labels.call_args.kwargs['remove'] == []


def test_no_label_status(pipeline, classifier, make_classification, gmail_client):
    classifier.classify.return_value = make_classification('personal', 0.9)
    r = pipeline.process('m1', apply=True)
    assert r.status is PipelineStatus.NO_LABEL
    assert r.applied is False
    assert r.decision.label_name is None
    assert r.classification.category == 'personal'
    gmail_client.modify_labels.assert_not_called()


def test_low_confidence_applies_uncertain_label(
    pipeline, classifier, make_classification, label_manager
):
    classifier.classify.return_value = make_classification('receipt', 0.2)
    r = pipeline.process('m1', apply=True)
    assert r.decision.reason is DecisionReason.LOW_CONFIDENCE
    label_manager.ensure.assert_called_once_with('Jev/Unsure')


# --- errors ---------------------------------------------------------------


@pytest.mark.parametrize(
    ('step', 'setup'),
    [
        (
            'fetch',
            lambda p: setattr(p._gmail.get_message, 'side_effect', GmailError('x')),
        ),
        (
            'skip_check',
            lambda p: setattr(p._labels.existing_ids, 'side_effect', GmailError('x')),
        ),
        (
            'anonymize',
            lambda p: setattr(p._anonymizer.anonymize, 'side_effect', GmailError('x')),
        ),
        (
            'classify',
            lambda p: setattr(p._classifier.classify, 'side_effect', GmailError('x')),
        ),
        ('apply', lambda p: setattr(p._labels.ensure, 'side_effect', GmailError('x'))),
    ],
)
def test_error_in_each_step(pipeline, step, setup):
    setup(pipeline)
    r = pipeline.process('m1', apply=True)
    assert r.status is PipelineStatus.ERROR
    assert r.error == f'{step}: GmailError: x'
    assert r.retryable is False
    assert r.applied is False


def test_error_in_modify_step(pipeline, gmail_client):
    gmail_client.modify_labels.side_effect = GmailError('boom', transient=True)
    r = pipeline.process('m1', apply=True)
    assert r.status is PipelineStatus.ERROR
    assert r.error == 'apply: GmailError: boom'
    assert r.retryable is True
    assert r.classification is not None
    assert r.decision is not None
    assert r.applied is False


def test_error_in_decide_step(pipeline, monkeypatch):
    from jev_gmail_labeler import pipeline as pl

    monkeypatch.setattr(pl, 'decide', MagicMock(side_effect=ValueError('bad')))
    r = pipeline.process('m1', apply=True)
    assert r.error == 'decide: ValueError: bad'
    assert r.retryable is False


def test_transient_classification_error_is_retryable(pipeline, classifier):
    classifier.classify.side_effect = ClassificationError('slow', transient=True)
    r = pipeline.process('m1', apply=True)
    assert r.status is PipelineStatus.ERROR
    assert r.retryable is True
    assert r.error == 'classify: ClassificationError: slow'
    assert r.subject == 'Your receipt'
    assert r.classification is None


def test_fetch_error_has_no_email_fields(pipeline, gmail_client):
    gmail_client.get_message.side_effect = GmailError('x', transient=True)
    r = pipeline.process('m1', apply=True)
    assert (r.thread_id, r.date, r.sender, r.subject) == (None, None, None, None)
    assert r.retryable is True


@pytest.mark.parametrize('exc', [AuthError('nope'), ConfigError('bad config')])
@pytest.mark.parametrize('where', ['gmail', 'classifier', 'anonymizer'])
def test_fatal_errors_propagate(pipeline, exc, where):
    target = {
        'gmail': pipeline._gmail.get_message,
        'classifier': pipeline._classifier.classify,
        'anonymizer': pipeline._anonymizer.anonymize,
    }[where]
    target.side_effect = exc
    with pytest.raises(type(exc)):
        pipeline.process('m1', apply=True)


# --- process_many, close, logging ----------------------------------------


def test_process_many_yields_in_order_and_is_lazy(pipeline, gmail_client, email_message):
    from dataclasses import replace

    gmail_client.get_message.side_effect = lambda mid: replace(email_message, id=mid)
    gen = pipeline.process_many(['a', 'b', 'c'], apply=False)
    gmail_client.get_message.assert_not_called()
    results = list(gen)
    assert [r.message_id for r in results] == ['a', 'b', 'c']
    assert all(r.status is PipelineStatus.WOULD_LABEL for r in results)


def test_process_many_isolates_failures(pipeline, gmail_client, email_message):
    gmail_client.get_message.side_effect = [GmailError('x'), email_message]
    statuses = [r.status for r in pipeline.process_many(['a', 'b'], apply=False)]
    assert statuses == [PipelineStatus.ERROR, PipelineStatus.WOULD_LABEL]


def test_process_many_passes_flags(pipeline, label_manager):
    label_manager.existing_ids.return_value = {'UNREAD'}
    results = list(pipeline.process_many(['a'], apply=False, force=True))
    assert results[0].status is PipelineStatus.WOULD_LABEL


def test_close_and_context_manager(pipeline, classifier):
    with pipeline as p:
        assert p is pipeline
        classifier.close.assert_not_called()
    classifier.close.assert_called_once()


def test_logs_one_info_line_and_subject_only_at_debug(pipeline, caplog):
    with caplog.at_level(logging.DEBUG, logger='jev_gmail_labeler.pipeline'):
        pipeline.process('m1', apply=True)
    info = [r for r in caplog.records if r.levelno == logging.INFO]
    debug = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert [r.getMessage() for r in info] == [
        'm1 labelled category=receipt label=Jev/Receipt confidence=0.90'
    ]
    assert 'Your receipt' not in info[0].getMessage()
    assert any('Your receipt' in r.getMessage() for r in debug)


def test_log_line_without_classification(pipeline, gmail_client, caplog):
    gmail_client.get_message.side_effect = MessageNotFoundError('gone')
    with caplog.at_level(logging.INFO, logger='jev_gmail_labeler.pipeline'):
        pipeline.process('m1', apply=True)
    assert caplog.records[0].getMessage() == 'm1 skipped category=- label=- confidence=-'


def test_log_line_with_decision_but_no_label(
    pipeline, classifier, make_classification, caplog
):
    classifier.classify.return_value = make_classification('personal', 0.9)
    with caplog.at_level(logging.INFO, logger='jev_gmail_labeler.pipeline'):
        pipeline.process('m1', apply=True)
    assert caplog.records[0].getMessage() == (
        'm1 no_label category=personal label=- confidence=0.90'
    )


def test_default_clock_is_utc(
    gmail_client, label_manager, anonymizer, classifier, criteria
):
    p = Pipeline(
        gmail=gmail_client,
        labels=label_manager,
        anonymizer=anonymizer,
        classifier=classifier,
        criteria=criteria,
        max_body_chars=100,
    )
    r = p.process('m1', apply=False)
    assert r.processed_at.endswith('+00:00')
