"""Disk-backed storage for the Test Data Service.

Hierarchy: **namespace → table → row**. A row is a record with an arbitrary set
of fields (dynamic "columns"), stored as a JSON object. Each namespace is one
JSON file on disk whose shape is::

    { "<table>": { "<row_id>": { "data": { ...fields }, "updated_at": "..." } } }

Writes are atomic (write-to-temp then rename) and guarded by a per-process lock.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class NamespaceNotFoundError(KeyError):
    """Raised when a namespace does not exist."""


class TableNotFoundError(KeyError):
    """Raised when a table does not exist."""


class TableExistsError(KeyError):
    """Raised when creating a table that already exists."""


class RowNotFoundError(KeyError):
    """Raised when a row id does not exist."""


class InvalidNamespaceError(ValueError):
    """Raised for a malformed or reserved namespace name."""


class InvalidTableError(ValueError):
    """Raised for a malformed table name."""


# Namespace names reserved because they collide with literal API routes.
RESERVED_NAMESPACES = {
    "namespaces",
    "health",
    "whoami",
    "keys",
    "login",
    "logout",
    "password",
    "me",
    "accounts",
}


@dataclass
class Row:
    id: str
    data: dict[str, Any]
    updated_at: str

    def to_dict(self) -> dict:
        return {"id": self.id, "updated_at": self.updated_at, "fields": self.data}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_name(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and not name.startswith(".")


def check_namespace(name: str) -> None:
    if not _valid_name(name):
        raise InvalidNamespaceError(f"Invalid namespace: {name!r}")
    if name in RESERVED_NAMESPACES:
        raise InvalidNamespaceError(f"Reserved namespace name: {name!r}")


def check_table(name: str) -> None:
    if not _valid_name(name):
        raise InvalidTableError(f"Invalid table name: {name!r}")


class StorageBackend(ABC):
    """Interface implemented by every storage backend (file, sqlite, …)."""

    # namespaces
    @abstractmethod
    def list_namespaces(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def create_namespace(self, namespace: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def delete_namespace(self, namespace: str) -> bool:
        raise NotImplementedError

    # tables
    @abstractmethod
    def list_tables(self, namespace: str) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def create_table(self, namespace: str, table: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete_table(self, namespace: str, table: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_columns(self, namespace: str, table: str) -> list[str]:
        """The table's designed (persistent) columns, in order."""
        raise NotImplementedError

    @abstractmethod
    def set_columns(self, namespace: str, table: str, columns: list[str]) -> list[str]:
        """Replace the table's designed columns."""
        raise NotImplementedError

    # rows
    @abstractmethod
    def list_rows(self, namespace: str, table: str) -> list[Row]:
        raise NotImplementedError

    @abstractmethod
    def get_row(self, namespace: str, table: str, row_id: str) -> Row:
        raise NotImplementedError

    @abstractmethod
    def create_row(self, namespace: str, table: str, fields: dict[str, Any]) -> Row:
        raise NotImplementedError

    @abstractmethod
    def update_row(self, namespace: str, table: str, row_id: str, fields: dict[str, Any]) -> Row:
        raise NotImplementedError

    @abstractmethod
    def delete_row(self, namespace: str, table: str, row_id: str) -> None:
        raise NotImplementedError

    def columns(self, namespace: str, table: str) -> list[str]:
        """Effective columns: the designed columns, then any extra row fields."""
        cols: list[str] = list(self.get_columns(namespace, table))
        for row in self.list_rows(namespace, table):
            for k in row.data:
                if k not in cols:
                    cols.append(k)
        return cols

    def patch_row(self, namespace: str, table: str, row_id: str, patch: dict[str, Any]) -> Row:
        """Partially update a row (JSON Merge Patch): given fields are set, a
        field whose value is ``None`` is removed, others are left untouched."""
        if not isinstance(patch, dict):
            raise ValueError("Patch body must be a JSON object.")
        current = self.get_row(namespace, table, row_id).data  # may raise Not*Found
        merged = dict(current)
        for key, value in patch.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        return self.update_row(namespace, table, row_id, merged)


class FileStorage(StorageBackend):
    """A namespaced, JSON-file-backed store of tables and dynamic-column rows."""

    def __init__(self, data_dir: str | os.PathLike[str]):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # -- file helpers --------------------------------------------------
    def _path(self, namespace: str) -> Path:
        check_namespace(namespace)
        return self.data_dir / f"{namespace}.json"

    def _load(self, namespace: str) -> dict[str, dict]:
        path = self._path(namespace)
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def _dump(self, namespace: str, data: dict[str, dict]) -> None:
        path = self._path(namespace)
        fd, tmp = tempfile.mkstemp(dir=self.data_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # -- namespaces ----------------------------------------------------
    def list_namespaces(self) -> list[str]:
        return sorted(p.stem for p in self.data_dir.glob("*.json"))

    def create_namespace(self, namespace: str) -> bool:
        """Create an (empty) namespace. Returns False if it already existed."""
        with self._lock:
            path = self._path(namespace)
            if path.exists():
                return False
            self._dump(namespace, {})
            return True

    def delete_namespace(self, namespace: str) -> bool:
        with self._lock:
            path = self._path(namespace)
            if not path.exists():
                return False
            path.unlink()
            return True

    # -- tables --------------------------------------------------------
    def list_tables(self, namespace: str) -> list[str]:
        with self._lock:
            return sorted(self._load(namespace).keys())

    def create_table(self, namespace: str, table: str) -> None:
        check_table(table)
        with self._lock:
            data = self._load(namespace)
            if table in data:
                raise TableExistsError(table)
            data[table] = {"columns": [], "rows": {}}
            self._dump(namespace, data)

    def delete_table(self, namespace: str, table: str) -> None:
        with self._lock:
            data = self._load(namespace)
            if table not in data:
                raise TableNotFoundError(table)
            del data[table]
            self._dump(namespace, data)

    @staticmethod
    def _rows(table_rec: dict) -> dict:
        return table_rec.get("rows", {})

    def get_columns(self, namespace: str, table: str) -> list[str]:
        with self._lock:
            data = self._load(namespace)
            if table not in data:
                raise TableNotFoundError(table)
            return list(data[table].get("columns", []))

    def set_columns(self, namespace: str, table: str, columns: list[str]) -> list[str]:
        with self._lock:
            data = self._load(namespace)
            if table not in data:
                raise TableNotFoundError(table)
            data[table]["columns"] = list(columns)
            self._dump(namespace, data)
            return list(columns)

    # -- rows ----------------------------------------------------------
    def list_rows(self, namespace: str, table: str) -> list[Row]:
        with self._lock:
            data = self._load(namespace)
            if table not in data:
                raise TableNotFoundError(table)
            rows = self._rows(data[table])
        return [self._to_row(rid, rec) for rid, rec in sorted(rows.items())]

    def get_row(self, namespace: str, table: str, row_id: str) -> Row:
        with self._lock:
            data = self._load(namespace)
            if table not in data:
                raise TableNotFoundError(table)
            rows = self._rows(data[table])
            if row_id not in rows:
                raise RowNotFoundError(row_id)
            return self._to_row(row_id, rows[row_id])

    def create_row(self, namespace: str, table: str, fields: dict[str, Any]) -> Row:
        if not isinstance(fields, dict):
            raise ValueError("Row fields must be a JSON object.")
        with self._lock:
            data = self._load(namespace)
            if table not in data:
                raise TableNotFoundError(table)
            row_id = secrets.token_hex(6)
            rec = {"data": dict(fields), "updated_at": _now()}
            data[table].setdefault("rows", {})[row_id] = rec
            self._dump(namespace, data)
            return self._to_row(row_id, rec)

    def update_row(self, namespace: str, table: str, row_id: str, fields: dict[str, Any]) -> Row:
        if not isinstance(fields, dict):
            raise ValueError("Row fields must be a JSON object.")
        with self._lock:
            data = self._load(namespace)
            if table not in data:
                raise TableNotFoundError(table)
            rows = self._rows(data[table])
            if row_id not in rows:
                raise RowNotFoundError(row_id)
            rec = {"data": dict(fields), "updated_at": _now()}
            rows[row_id] = rec
            self._dump(namespace, data)
            return self._to_row(row_id, rec)

    def delete_row(self, namespace: str, table: str, row_id: str) -> None:
        with self._lock:
            data = self._load(namespace)
            if table not in data:
                raise TableNotFoundError(table)
            rows = self._rows(data[table])
            if row_id not in rows:
                raise RowNotFoundError(row_id)
            del rows[row_id]
            self._dump(namespace, data)

    @staticmethod
    def _to_row(row_id: str, rec: dict) -> Row:
        return Row(id=row_id, data=rec.get("data", {}), updated_at=rec.get("updated_at", ""))


# Backward-compatible alias (the file backend was previously named ``Storage``).
Storage = FileStorage


def open_storage(config) -> StorageBackend:
    """Build the storage backend named by ``config.storage`` ('sqlite' | 'file')."""
    kind = (getattr(config, "storage", "sqlite") or "sqlite").lower()
    if kind == "file":
        return FileStorage(config.data_dir)
    if kind == "sqlite":
        from .sqlite_storage import SqliteStorage

        db_path = config.db_path or os.path.join(config.data_dir, "tds.db")
        Path(config.data_dir).mkdir(parents=True, exist_ok=True)
        return SqliteStorage(db_path)
    raise ValueError(f"Unknown storage backend: {kind!r} (use 'sqlite' or 'file').")
