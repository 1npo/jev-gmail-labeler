"""Shared guards and fixtures. Tests must never touch the disk or the network."""

import builtins
import io
import os
import site
import sqlite3
import sys
import sysconfig

import pytest

from jev_gmail_labeler.criteria import parse_criteria
from jev_gmail_labeler.models import AnonymizedEmail, EmailMessage


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
