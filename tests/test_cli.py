import pytest

from jev_gmail_labeler import __version__, cli


def test_main_returns_zero():
    assert cli.main([]) == 0


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(['--version'])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out
