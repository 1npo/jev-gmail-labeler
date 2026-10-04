import json
from pathlib import Path
from unittest.mock import MagicMock, mock_open

import pytest

from jev_gmail_labeler import files


def test_read_text(monkeypatch):
    m = mock_open(read_data='hello')
    monkeypatch.setattr(files, 'open', m, raising=False)
    assert files.read_text(Path('/x/a.txt')) == 'hello'
    m.assert_called_once_with(Path('/x/a.txt'), encoding='utf-8')


def test_read_text_missing(monkeypatch):
    monkeypatch.setattr(
        files, 'open', MagicMock(side_effect=FileNotFoundError), raising=False
    )
    with pytest.raises(FileNotFoundError):
        files.read_text(Path('/x/missing'))


def test_read_json(monkeypatch):
    monkeypatch.setattr(files, 'open', mock_open(read_data='{"a": 1}'), raising=False)
    assert files.read_json(Path('/x/a.json')) == {'a': 1}


def test_read_json_invalid(monkeypatch):
    monkeypatch.setattr(files, 'open', mock_open(read_data='{nope'), raising=False)
    with pytest.raises(ValueError, match='/x/a.json: invalid JSON'):
        files.read_json(Path('/x/a.json'))


def test_ensure_dir():
    p = MagicMock(spec=Path)
    files.ensure_dir(p)
    p.mkdir.assert_called_once_with(parents=True, exist_ok=True, mode=0o700)
    files.ensure_dir(p, 0o755)
    p.mkdir.assert_called_with(parents=True, exist_ok=True, mode=0o755)


def test_write_text_atomic_with_mode(monkeypatch):
    m = mock_open()
    fake_os = MagicMock()
    ensure = MagicMock()
    monkeypatch.setattr(files, 'open', m, raising=False)
    monkeypatch.setattr(files, 'os', fake_os)
    monkeypatch.setattr(files, 'ensure_dir', ensure)
    files.write_text_atomic(Path('/x/dir/token.json'), 'data', mode=0o600)
    ensure.assert_called_once_with(Path('/x/dir'))
    tmp = Path('/x/dir/token.json.tmp')
    m.assert_called_once_with(tmp, 'w', encoding='utf-8', newline='')
    m().write.assert_called_once_with('data')
    fake_os.chmod.assert_called_once_with(tmp, 0o600)
    fake_os.replace.assert_called_once_with(tmp, Path('/x/dir/token.json'))


def test_write_text_atomic_without_mode(monkeypatch):
    fake_os = MagicMock()
    monkeypatch.setattr(files, 'open', mock_open(), raising=False)
    monkeypatch.setattr(files, 'os', fake_os)
    monkeypatch.setattr(files, 'ensure_dir', MagicMock())
    files.write_text_atomic(Path('/x/a.json'), json.dumps({}))
    fake_os.chmod.assert_not_called()
    fake_os.replace.assert_called_once()
