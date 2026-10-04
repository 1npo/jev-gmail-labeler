import os
import socket
import sqlite3

import pytest
import pytest_socket
from tests.conftest import DiskIOBlocked


def test_open_for_write_is_blocked():
    with pytest.raises(DiskIOBlocked):
        open('x', 'w').close()


def test_open_for_read_outside_libraries_is_blocked():
    with pytest.raises(DiskIOBlocked):
        open('/etc/hostname').close()


def test_open_fd_is_allowed():
    assert isinstance(os.devnull, str)


def test_os_replace_is_blocked():
    with pytest.raises(DiskIOBlocked):
        os.replace('a', 'b')


def test_sqlite_file_is_blocked():
    with pytest.raises(DiskIOBlocked):
        sqlite3.connect('f.db')


def test_sqlite_memory_is_allowed():
    sqlite3.connect(':memory:').close()


def test_network_is_blocked():
    with pytest.raises(pytest_socket.SocketBlockedError):
        socket.create_connection(('example.com', 80))
