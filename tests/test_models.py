import json

import pytest

from jev_gmail_labeler.models import (
    AnonymizedEmail,
    ClassificationResult,
    DecisionReason,
    EmailMessage,
    LabelDecision,
    PipelineResult,
    PipelineStatus,
    SkipReason,
)


@pytest.fixture
def classification():
    return ClassificationResult(
        message_id='m1',
        category='receipt',
        confidence=0.9,
        probabilities={'receipt': 0.9, '_none': 0.1},
        model='jev-1.13.0',
        request_id='req-1',
        input_tokens=1000,
        cost_usd=0.000042,
        elapsed_ms=120.5,
    )


@pytest.fixture
def decision():
    return LabelDecision('m1', 'receipt', DecisionReason.MATCHED, 'Jev/Receipt')


def _result(**kw):
    base = dict(
        message_id='m1',
        thread_id='t1',
        date='d',
        sender='s',
        subject='sub',
        status=PipelineStatus.SKIPPED,
        skip_reason=SkipReason.ALREADY_LABELLED,
        classification=None,
        decision=None,
        applied=False,
        label_id=None,
        error=None,
        retryable=False,
        processed_at='2026-10-03T12:00:00+00:00',
    )
    base.update(kw)
    return PipelineResult(**base)


def test_email_message_round_trip(email_message):
    data = email_message.to_dict()
    assert data['label_ids'] == ['INBOX', 'UNREAD']
    json.dumps(data)
    assert EmailMessage.from_dict(data) == email_message


def test_anonymized_round_trip(anonymized_email):
    data = anonymized_email.to_dict()
    json.dumps(data)
    assert AnonymizedEmail.from_dict(data) == anonymized_email


def test_to_jev_state_keys(anonymized_email):
    assert list(anonymized_email.to_jev_state()) == [
        'from',
        'to',
        'reply_to',
        'date',
        'subject',
        'body',
    ]


def test_classification_round_trip(classification):
    data = classification.to_dict()
    json.dumps(data)
    assert ClassificationResult.from_dict(data) == classification


def test_decision_round_trip(decision):
    data = decision.to_dict()
    assert data['reason'] == 'matched'
    assert type(data['reason']) is str
    assert LabelDecision.from_dict(data) == decision


def test_result_round_trip_minimal():
    r = _result()
    data = r.to_dict()
    assert data['status'] == 'skipped'
    assert data['skip_reason'] == 'already_labelled'
    json.dumps(data)
    assert PipelineResult.from_dict(data) == r


def test_result_round_trip_full(classification, decision):
    r = _result(
        status=PipelineStatus.LABELLED,
        skip_reason=None,
        classification=classification,
        decision=decision,
        applied=True,
        label_id='Label_1',
    )
    data = r.to_dict()
    assert data['classification']['category'] == 'receipt'
    assert data['decision']['label_name'] == 'Jev/Receipt'
    json.dumps(data)
    assert PipelineResult.from_dict(data) == r
