import runpy
from unittest.mock import MagicMock

import pytest

from jev_gmail_labeler import cli


def test_module_runs_main_and_exits_with_its_code(monkeypatch):
    monkeypatch.setattr(cli, 'main', MagicMock(return_value=7))
    with pytest.raises(SystemExit) as e:
        runpy.run_module('jev_gmail_labeler', run_name='__main__')
    assert e.value.code == 7
