from unittest.mock import MagicMock

import pytest

from jev_gmail_labeler.errors import GmailError
from jev_gmail_labeler.google_api.gmail import GmailClient, GmailLabel
from jev_gmail_labeler.google_api.labels import LabelManager


def label(id, name):
    return GmailLabel(id, name, 'user')


@pytest.fixture
def gmail():
    g = MagicMock(spec=GmailClient)
    g.list_labels.return_value = [label('L1', 'Jev/Receipt'), label('INBOX', 'INBOX')]
    return g


@pytest.fixture
def manager(gmail):
    return LabelManager(gmail)


def test_find_id_is_case_insensitive(manager):
    assert manager.find_id('jev/RECEIPT') == 'L1'
    assert manager.find_id('Jev/Nope') is None


def test_cache_loaded_once(manager, gmail):
    manager.find_id('a')
    manager.find_id('b')
    manager.existing_ids(['c'])
    gmail.list_labels.assert_called_once()


def test_ensure_existing_does_not_create(manager, gmail):
    assert manager.ensure('Jev/Receipt') == 'L1'
    gmail.create_label.assert_not_called()


def test_ensure_creates_missing_ancestors_in_order(manager, gmail):
    gmail.create_label.side_effect = lambda name: label(f'id:{name}', name)
    assert manager.ensure('Jev/Sub/Deep') == 'id:Jev/Sub/Deep'
    assert [c.args[0] for c in gmail.create_label.call_args_list] == [
        'Jev',
        'Jev/Sub',
        'Jev/Sub/Deep',
    ]
    assert manager.find_id('jev/sub') == 'id:Jev/Sub'


def test_ensure_creates_only_missing_parts(manager, gmail):
    gmail.create_label.side_effect = lambda name: label(f'id:{name}', name)
    manager.ensure('Jev/Receipt/Old')
    # 'Jev' is not in the fixture's label list, 'Jev/Receipt' is.
    assert [c.args[0] for c in gmail.create_label.call_args_list] == [
        'Jev',
        'Jev/Receipt/Old',
    ]


def test_ensure_uses_cache_after_create(manager, gmail):
    gmail.create_label.side_effect = lambda name: label(f'id:{name}', name)
    manager.ensure('Mail')
    manager.ensure('Mail')
    gmail.create_label.assert_called_once()
    gmail.list_labels.assert_called_once()


def test_409_reloads_cache_and_returns_existing(manager, gmail):
    manager.find_id('x')  # load cache
    gmail.create_label.side_effect = GmailError('exists', status=409)
    gmail.list_labels.return_value = [label('L9', 'Mail')]
    assert manager.ensure('Mail') == 'L9'
    assert gmail.list_labels.call_count == 2


def test_409_without_label_reraises(manager, gmail):
    gmail.create_label.side_effect = GmailError('exists', status=409)
    with pytest.raises(GmailError):
        manager.ensure('Nope')


def test_other_gmail_error_reraises_without_reload(manager, gmail):
    gmail.create_label.side_effect = GmailError('boom', status=500, transient=True)
    with pytest.raises(GmailError):
        manager.ensure('Nope')
    gmail.list_labels.assert_called_once()


def test_existing_ids_never_creates(manager, gmail):
    assert manager.existing_ids(['Jev/Receipt', 'inbox', 'Jev/Missing']) == {
        'L1',
        'INBOX',
    }
    gmail.create_label.assert_not_called()
    assert manager.existing_ids([]) == set()
