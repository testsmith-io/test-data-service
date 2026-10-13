"""SQLite storage backend.

Same namespace → table → row model as the file backend, but persisted in a
single SQLite database (created automatically on first use). Rows keep their
dynamic fields in a JSON column, so the SQL schema is fixed while the data stays
schema-free. WAL mode + a per-process lock keep concurrent requests safe.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from .storage import (
    Row,
    RowNotFoundError,
    StorageBackend,
    TableExistsError,
    TableNotFoundError,
    check_namespace,
    check_table,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS namespaces (name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS tables (
    namespace TEXT NOT NULL,
    tbl TEXT NOT NULL,
    PRIMARY KEY (namespace, tbl)
);
CREATE TABLE IF NOT EXISTS rows (
    namespace TEXT NOT NULL,
    tbl TEXT NOT NULL,
    id TEXT NOT NULL,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (namespace, tbl, id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteStorage(StorageBackend):
    def __init__(self, db_path: str | os.PathLike[str]):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        # check_same_thread=False: one connection shared across worker threads,
        # serialized by our own lock.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._conn:
            self._conn.executescript(_SCHEMA)

    # -- helpers -------------------------------------------------------
    def _table_exists(self, namespace: str, table: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM tables WHERE namespace=? AND tbl=?", (namespace, table)
        )
        return cur.fetchone() is not None

    # -- namespaces ----------------------------------------------------
    def list_namespaces(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self._conn.execute("SELECT name FROM namespaces ORDER BY name")]

    def create_namespace(self, namespace: str) -> bool:
        check_namespace(namespace)
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO namespaces(name) VALUES(?)", (namespace,)
            )
            return cur.rowcount > 0

    def delete_namespace(self, namespace: str) -> bool:
        check_namespace(namespace)
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM namespaces WHERE name=?", (namespace,))
            self._conn.execute("DELETE FROM tables WHERE namespace=?", (namespace,))
            self._conn.execute("DELETE FROM rows WHERE namespace=?", (namespace,))
            return cur.rowcount > 0

    # -- tables --------------------------------------------------------
    def list_tables(self, namespace: str) -> list[str]:
        check_namespace(namespace)
        with self._lock:
            return [
                r[0]
                for r in self._conn.execute(
                    "SELECT tbl FROM tables WHERE namespace=? ORDER BY tbl", (namespace,)
                )
            ]

    def create_table(self, namespace: str, table: str) -> None:
        check_namespace(namespace)
        check_table(table)
        with self._lock, self._conn:
            if self._table_exists(namespace, table):
                raise TableExistsError(table)
            self._conn.execute("INSERT OR IGNORE INTO namespaces(name) VALUES(?)", (namespace,))
            self._conn.execute(
                "INSERT INTO tables(namespace, tbl) VALUES(?, ?)", (namespace, table)
            )

    def delete_table(self, namespace: str, table: str) -> None:
        check_namespace(namespace)
        with self._lock, self._conn:
            if not self._table_exists(namespace, table):
                raise TableNotFoundError(table)
            self._conn.execute("DELETE FROM tables WHERE namespace=? AND tbl=?", (namespace, table))
            self._conn.execute("DELETE FROM rows WHERE namespace=? AND tbl=?", (namespace, table))

    # -- rows ----------------------------------------------------------
    def list_rows(self, namespace: str, table: str) -> list[Row]:
        check_namespace(namespace)
        with self._lock:
            if not self._table_exists(namespace, table):
                raise TableNotFoundError(table)
            cur = self._conn.execute(
                "SELECT id, data, updated_at FROM rows WHERE namespace=? AND tbl=? ORDER BY id",
                (namespace, table),
            )
            return [Row(id=r[0], data=json.loads(r[1]), updated_at=r[2]) for r in cur]

    def get_row(self, namespace: str, table: str, row_id: str) -> Row:
        check_namespace(namespace)
        with self._lock:
            if not self._table_exists(namespace, table):
                raise TableNotFoundError(table)
            row = self._conn.execute(
                "SELECT id, data, updated_at FROM rows WHERE namespace=? AND tbl=? AND id=?",
                (namespace, table, row_id),
            ).fetchone()
            if row is None:
                raise RowNotFoundError(row_id)
            return Row(id=row[0], data=json.loads(row[1]), updated_at=row[2])

    def create_row(self, namespace: str, table: str, fields: dict[str, Any]) -> Row:
        if not isinstance(fields, dict):
            raise ValueError("Row fields must be a JSON object.")
        check_namespace(namespace)
        with self._lock, self._conn:
            if not self._table_exists(namespace, table):
                raise TableNotFoundError(table)
            row_id = secrets.token_hex(6)
            ts = _now()
            self._conn.execute(
                "INSERT INTO rows(namespace, tbl, id, data, updated_at) VALUES(?,?,?,?,?)",
                (namespace, table, row_id, json.dumps(fields), ts),
            )
            return Row(id=row_id, data=dict(fields), updated_at=ts)

    def update_row(self, namespace: str, table: str, row_id: str, fields: dict[str, Any]) -> Row:
        if not isinstance(fields, dict):
            raise ValueError("Row fields must be a JSON object.")
        check_namespace(namespace)
        with self._lock, self._conn:
            if not self._table_exists(namespace, table):
                raise TableNotFoundError(table)
            ts = _now()
            cur = self._conn.execute(
                "UPDATE rows SET data=?, updated_at=? WHERE namespace=? AND tbl=? AND id=?",
                (json.dumps(fields), ts, namespace, table, row_id),
            )
            if cur.rowcount == 0:
                raise RowNotFoundError(row_id)
            return Row(id=row_id, data=dict(fields), updated_at=ts)

    def delete_row(self, namespace: str, table: str, row_id: str) -> None:
        check_namespace(namespace)
        with self._lock, self._conn:
            if not self._table_exists(namespace, table):
                raise TableNotFoundError(table)
            cur = self._conn.execute(
                "DELETE FROM rows WHERE namespace=? AND tbl=? AND id=?",
                (namespace, table, row_id),
            )
            if cur.rowcount == 0:
                raise RowNotFoundError(row_id)
