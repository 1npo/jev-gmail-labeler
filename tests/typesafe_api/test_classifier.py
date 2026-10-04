from unittest.mock import MagicMock

import httpx2
import pytest
from typesafe_sdk import (
    Choice,
    RetryPolicy,
    TypeSafeAPIConnectionError,
    TypeSafeAPIResponseValidationError,
    TypeSafeAPITimeoutError,
    TypeSafeAuthenticationError,
    TypeSafeBadRequestError,
    TypeSafeError,
    TypeSafeInternalServerError,
    TypeSafeNotFoundError,
    TypeSafePermissionDeniedError,
    TypeSafeRateLimitError,
    TypeSafeUnprocessableEntityError,
)

from jev_gmail_labeler import logging_setup
from jev_gmail_labeler.errors import AuthError, ClassificationError
from jev_gmail_labeler.typesafe_api import classifier as clf
from jev_gmail_labeler.typesafe_api.classifier import JevClassifier


@pytest.fixture(autouse=True)
def clean_secrets(monkeypatch):
    monkeypatch.setattr(logging_setup, '_secrets', set())


@pytest.fixture
def jev(criteria, typesafe_client):
    return JevClassifier(criteria, api_key='key-1234567890', client=typesafe_client)


def api_error(cls, status=400):
    if cls is TypeSafeAPIResponseValidationError:
        return cls(200, {}, httpx2.Headers(), 'choices.category')
    return cls(status, {}, httpx2.Headers(), 'boom')


def test_build_questions(criteria):
    questions = clf.build_questions(criteria)
    assert list(questions) == ['category']
    q = questions['category']
    assert isinstance(q, Choice)
    assert q.instructions == criteria.instructions
    assert list(q.criteria) == ['receipt', 'news', 'personal', '_none']


def test_cost_usd():
    assert clf.cost_usd(None) is None
    assert clf.cost_usd(1_000_000) == pytest.approx(0.042)
    assert clf.cost_usd(0) == 0


def test_classify_maps_result(jev, typesafe_client, anonymized_email, criteria):
    result = jev.classify(anonymized_email)
    kwargs = typesafe_client.system_one.call_args.kwargs
    assert kwargs['state'] == anonymized_email.to_jev_state()
    assert list(kwargs['questions']['category'].criteria) == list(
        criteria.choice_criteria()
    )
    assert result.message_id == 'm1'
    assert result.category == 'receipt'
    assert result.confidence == 0.9
    assert result.probabilities == {'receipt': 0.9, '_none': 0.1}
    assert result.model == 'jev-1.13.0'
    assert result.request_id == 'req-1'
    assert result.input_tokens == 1000
    assert result.cost_usd == pytest.approx(0.000042)
    assert result.elapsed_ms >= 0


def test_classify_none_choice_is_valid(
    jev, typesafe_client, make_jev_response, anonymized_email
):
    typesafe_client.system_one.return_value = make_jev_response('_none', 0.7)
    assert jev.classify(anonymized_email).category == '_none'


def test_classify_without_usage_tokens(
    jev, typesafe_client, make_jev_response, anonymized_email
):
    typesafe_client.system_one.return_value = make_jev_response(input_tokens=None)
    result = jev.classify(anonymized_email)
    assert result.input_tokens is None
    assert result.cost_usd is None


def test_unknown_choice(jev, typesafe_client, make_jev_response, anonymized_email):
    typesafe_client.system_one.return_value = make_jev_response('bogus')
    with pytest.raises(ClassificationError, match='unknown category "bogus"') as e:
        jev.classify(anonymized_email)
    assert e.value.transient is False


@pytest.mark.parametrize(
    'cls', [TypeSafeAuthenticationError, TypeSafePermissionDeniedError]
)
def test_auth_errors(jev, typesafe_client, anonymized_email, cls):
    typesafe_client.system_one.side_effect = api_error(cls, 401)
    with pytest.raises(AuthError, match='TypeSafe API key was rejected'):
        jev.classify(anonymized_email)


@pytest.mark.parametrize(
    'cls',
    [TypeSafeBadRequestError, TypeSafeUnprocessableEntityError, TypeSafeNotFoundError],
)
def test_permanent_errors(jev, typesafe_client, anonymized_email, cls):
    typesafe_client.system_one.side_effect = api_error(cls)
    with pytest.raises(ClassificationError) as e:
        jev.classify(anonymized_email)
    assert e.value.transient is False
    assert str(e.value).startswith(f'{cls.__name__}:')


@pytest.mark.parametrize(
    'exc',
    [
        api_error(TypeSafeRateLimitError, 429),
        api_error(TypeSafeInternalServerError, 500),
        api_error(TypeSafeAPIResponseValidationError),
        TypeSafeAPIConnectionError('refused'),
        TypeSafeAPITimeoutError(10.0),
    ],
)
def test_transient_errors(jev, typesafe_client, anonymized_email, exc):
    typesafe_client.system_one.side_effect = exc
    with pytest.raises(ClassificationError) as e:
        jev.classify(anonymized_email)
    assert e.value.transient is True


def test_other_typesafe_error_is_permanent(jev, typesafe_client, anonymized_email):
    typesafe_client.system_one.side_effect = TypeSafeError('weird')
    with pytest.raises(ClassificationError) as e:
        jev.classify(anonymized_email)
    assert e.value.transient is False


def test_non_sdk_errors_propagate(jev, typesafe_client, anonymized_email):
    typesafe_client.system_one.side_effect = KeyError('x')
    with pytest.raises(KeyError):
        jev.classify(anonymized_email)


def test_client_built_with_retry_policy(monkeypatch, criteria):
    ctor = MagicMock()
    monkeypatch.setattr(clf, 'TypeSafeClient', ctor)
    JevClassifier(criteria, api_key='key-1234567890', model='jev-1.13.0', timeout=3.0)
    ctor.assert_called_once_with(
        api_key='key-1234567890',
        model='jev-1.13.0',
        timeout=3.0,
        retry=RetryPolicy(max_retries=4, timeout=60.0),
    )


def test_defaults_when_building_client(monkeypatch, criteria):
    ctor = MagicMock()
    monkeypatch.setattr(clf, 'TypeSafeClient', ctor)
    JevClassifier(criteria, api_key='key-1234567890')
    kwargs = ctor.call_args.kwargs
    assert (kwargs['model'], kwargs['timeout']) == ('jev-latest', 10.0)


def test_api_key_registered_as_secret(criteria, typesafe_client):
    JevClassifier(criteria, api_key='key-1234567890', client=typesafe_client)
    assert 'key-1234567890' in logging_setup._secrets


def test_close_and_context_manager(criteria, typesafe_client):
    with JevClassifier(criteria, api_key='key-1234567890', client=typesafe_client) as j:
        assert isinstance(j, JevClassifier)
        typesafe_client.close.assert_not_called()
    typesafe_client.close.assert_called_once()
