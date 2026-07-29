"""Shared test scaffolding.

Every DB test needs the same thing: an in-memory database carrying the real
schema (plus migrations), row access by name, and foreign keys enforced — the
same posture as production `storage.connect`. Before this existed, nine test
files each carried their own copy of that setup, and they had already drifted
(some forgot `PRAGMA foreign_keys`, silently disabling the constraint tests
rely on).
"""
from __future__ import annotations

import contextlib
import io
import sqlite3
import unittest

from fli import storage


def memory_db() -> sqlite3.Connection:
    """An in-memory DB with the authoritative schema and migrations applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # init_db narrates column migrations; that is signal in production and
    # noise when repeated by every test, so it is swallowed here.
    with contextlib.redirect_stdout(io.StringIO()):
        storage.init_db(conn)
    return conn


class DBTestCase(unittest.TestCase):
    """Base class for tests that need a schema-loaded database.

    Subclasses that need seed rows override setUp, call super().setUp() first,
    and insert their own.
    """

    def setUp(self):
        self.conn = memory_db()

    def tearDown(self):
        self.conn.close()
