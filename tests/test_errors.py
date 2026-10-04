import pytest

from jev_gmail_labeler import errors


@pytest.mark.parametrize(
    'cls',
    [
        errors.LabelerError,
        errors.ConfigError,
        errors.AuthError,
        errors.GmailError,
        errors.MessageNotFoundError,
        errors.HistoryExpiredError,
        errors.PubSubError,
        errors.ClassificationError,
        errors.AnonymizationError,
    ],
)
def test_default_not_transient_and_override(cls):
    assert cls('x').transient is False
    assert cls('x', transient=True).transient is True


def test_hierarchy():
    assert issubclass(errors.CriteriaError, errors.ConfigError)
    assert issubclass(errors.MessageNotFoundError, errors.GmailError)
    assert issubclass(errors.HistoryExpiredError, errors.GmailError)
    assert issubclass(errors.ConfigError, errors.LabelerError)


def test_gmail_error_status():
    assert errors.GmailError('x').status is None
    assert errors.GmailError('x', status=429, transient=True).status == 429


def test_criteria_error_str():
    e = errors.CriteriaError(['a: bad', 'b: worse'], 'criteria.json')
    assert e.errors == ['a: bad', 'b: worse']
    assert str(e) == 'criteria.json:\n  a: bad\n  b: worse'
