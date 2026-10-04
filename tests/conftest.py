"""Shared guards and fixtures. Tests must never touch the disk or the network."""

import base64
import builtins
import io
import json
import os
import site
import sqlite3
import sys
import sysconfig
from types import SimpleNamespace
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from jev_gmail_labeler.anonymize import Anonymizer
from jev_gmail_labeler.criteria import parse_criteria
from jev_gmail_labeler.google_api.gmail import GmailClient
from jev_gmail_labeler.google_api.labels import LabelManager
from jev_gmail_labeler.google_api.pubsub import PulledMessage
from jev_gmail_labeler.models import AnonymizedEmail, ClassificationResult, EmailMessage
from jev_gmail_labeler.state import StateStore


class DiskIOBlocked(AssertionError):
    """Raised when a test tries to touch the disk."""


@pytest.fixture(autouse=True)
def _block_disk_io(monkeypatch):
    roots = tuple(
        {
            os.path.abspath(p)
            for p in (
                sys.prefix,
                sys.base_prefix,
                sysconfig.get_paths()['stdlib'],
                *site.getsitepackages(),
            )
        }
    )
    real_open = builtins.open

    def guarded_open(file, mode='r', *args, **kwargs):
        if isinstance(file, int):
            return real_open(file, mode, *args, **kwargs)
        path = os.path.abspath(os.fspath(file))
        # Read-only library data files are fine.
        if not any(c in mode for c in 'wax+') and path.startswith(roots):
            return real_open(file, mode, *args, **kwargs)
        raise DiskIOBlocked(
            f'test tried to open {file!r} (mode {mode!r}); mock jev_gmail_labeler.files'
        )

    def blocked(*a, **k):
        raise DiskIOBlocked(f'disk write blocked: {a!r}')

    real_connect = sqlite3.connect

    def guarded_connect(database, *a, **k):
        if database != ':memory:':
            raise DiskIOBlocked(f'sqlite file {database!r}')
        return real_connect(database, *a, **k)

    monkeypatch.setattr(builtins, 'open', guarded_open)
    monkeypatch.setattr(io, 'open', guarded_open)
    for name in ('replace', 'remove', 'unlink', 'mkdir', 'makedirs', 'rename', 'chmod'):
        monkeypatch.setattr(os, name, blocked)
    monkeypatch.setattr(sqlite3, 'connect', guarded_connect)


@pytest.fixture
def email_message() -> EmailMessage:
    return EmailMessage(
        id='m1',
        thread_id='t1',
        history_id='100',
        internal_date_ms=1_700_000_000_000,
        label_ids=('INBOX', 'UNREAD'),
        sender='Alice <alice@example.com>',
        to='me@example.com',
        cc='',
        reply_to='',
        date='Tue, 3 Oct 2026 12:00:00 +0000',
        subject='Your receipt',
        snippet='Thanks for your order',
        body_text='Hello Alice, here is your receipt.',
    )


@pytest.fixture
def anonymized_email() -> AnonymizedEmail:
    return AnonymizedEmail(
        id='m1',
        sender='Alice <alice@example.com>',
        to='me@example.com',
        reply_to='',
        date='Tue, 3 Oct 2026 12:00:00 +0000',
        subject='Your receipt',
        body='Hello <PERSON>, here is your receipt.',
        body_truncated=False,
    )


@pytest.fixture
def criteria_data() -> dict:
    return {
        'version': 1,
        'min_confidence': 0.5,
        'uncertain_label': 'Unsure',
        'categories': [
            {'id': 'receipt', 'description': 'A receipt for a purchase'},
            {'id': 'news', 'description': 'A news article', 'label': 'Reading'},
            {'id': 'personal', 'description': 'A personal email', 'label': None},
        ],
    }


@pytest.fixture
def criteria(criteria_data):
    return parse_criteria(criteria_data)


def _b64(text: str, charset: str = 'utf-8') -> str:
    return base64.urlsafe_b64encode(text.encode(charset)).decode().rstrip('=')


@pytest.fixture
def make_raw_message():
    """Build a Gmail API ``format=full`` message resource."""

    def factory(
        id='m1',
        subject='Hi',
        sender='A <a@x.com>',
        plain=None,
        html=None,
        label_ids=('INBOX',),
        attachment=False,
        snippet='',
    ) -> dict:
        text_parts = []
        if plain is not None:
            text_parts.append(
                {'mimeType': 'text/plain', 'filename': '', 'body': {'data': _b64(plain)}}
            )
        if html is not None:
            text_parts.append(
                {'mimeType': 'text/html', 'filename': '', 'body': {'data': _b64(html)}}
            )
        parts = [
            {'mimeType': 'multipart/alternative', 'filename': '', 'parts': text_parts}
        ]
        if attachment:
            parts.append(
                {
                    'mimeType': 'application/pdf',
                    'filename': 'bill.pdf',
                    'body': {'attachmentId': 'att1', 'size': 10},
                }
            )
        return {
            'id': id,
            'threadId': f't-{id}',
            'historyId': '100',
            'internalDate': '1700000000000',
            'labelIds': list(label_ids),
            'snippet': snippet,
            'payload': {
                'mimeType': 'multipart/mixed',
                'headers': [
                    {'name': 'From', 'value': sender},
                    {'name': 'To', 'value': 'me@example.com'},
                    {'name': 'Subject', 'value': subject},
                    {'name': 'Date', 'value': 'Tue, 3 Oct 2026 12:00:00 +0000'},
                ],
                'parts': parts,
            },
        }

    return factory


class FakeClock:
    """A clock whose ``sleep`` advances time instead of blocking."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def gmail_service() -> MagicMock:
    return MagicMock(name='gmail_service')


@pytest.fixture
def api():
    """``api(service, 'messages.get')`` -> mock of ``service.users().messages().get``."""

    def get(service, path):
        users = service.users()
        if '.' not in path:
            return getattr(users, path)
        resource, method = path.split('.')
        return getattr(getattr(users, resource)(), method)

    return get


@pytest.fixture
def make_http_error():
    def factory(status: int, reason: str | None = None) -> HttpError:
        body = json.dumps({'error': {'message': reason or f'error {status}'}}).encode()
        return HttpError(httplib2.Response({'status': status}), body)

    return factory


@pytest.fixture
def make_jev_response():
    def factory(
        choice='receipt', confidence=0.9, input_tokens=1000, probabilities=None
    ) -> SimpleNamespace:
        probs = probabilities or {choice: confidence, '_none': round(1 - confidence, 6)}
        return SimpleNamespace(
            model='jev-1.13.0',
            request_id='req-1',
            usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=1),
            choices={
                'category': SimpleNamespace(
                    choice=choice, confidence=confidence, probabilities=probs
                )
            },
        )

    return factory


@pytest.fixture
def typesafe_client(make_jev_response) -> MagicMock:
    client = MagicMock(name='typesafe_client')
    client.system_one.return_value = make_jev_response()
    return client


@pytest.fixture
def make_classification():
    def factory(
        category='receipt', confidence=0.9, message_id='m1'
    ) -> ClassificationResult:
        return ClassificationResult(
            message_id=message_id,
            category=category,
            confidence=confidence,
            probabilities={category: confidence, '_none': 1 - confidence},
            model='jev-1.13.0',
            request_id='req-1',
            input_tokens=1000,
            cost_usd=0.000042,
            elapsed_ms=50.0,
        )

    return factory


@pytest.fixture
def gmail_client(email_message) -> MagicMock:
    client = MagicMock(spec=GmailClient)
    client.get_message.return_value = email_message
    return client


@pytest.fixture
def label_manager() -> MagicMock:
    manager = MagicMock(spec=LabelManager)
    manager.existing_ids.return_value = set()
    manager.find_id.return_value = None
    manager.ensure.return_value = 'L-new'
    return manager


@pytest.fixture
def anonymizer(anonymized_email) -> MagicMock:
    mock = MagicMock(spec=Anonymizer)
    mock.anonymize.return_value = anonymized_email
    return mock


@pytest.fixture
def memory_state(fake_clock):
    store = StateStore(':memory:', clock=fake_clock.time)
    yield store
    store.close()


@pytest.fixture
def make_pulled():
    def factory(history_id, email='me@example.com', ack_id='a1') -> PulledMessage:
        data = json.dumps({'emailAddress': email, 'historyId': history_id}).encode()
        return PulledMessage(ack_id=ack_id, data=data, message_id=f'pm-{ack_id}')

    return factory
