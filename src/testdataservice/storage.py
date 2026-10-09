"""Disk-backed storage for the Test Data Service.

Each namespace is a single JSON file on disk. Entries are key/value pairs with
an explicit value type. Writes are atomic (write-to-temp then rename) and
guarded by a per-process lock so concurrent requests don't corrupt the file.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .types import ValueType, infer_type, validate


class KeyNotFoundError(KeyError):
    """Raised when a requested key does not exist."""


class KeyExistsError(KeyError):
    """Raised when creating a key that already exists."""


class InvalidNamespaceError(ValueError):
    """Raised for a malformed or reserved namespace name."""


class InvalidKeyError(ValueError):
    """Raised for a malformed (e.g. blank) key."""


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
class Entry:
    key: str
    value: Any
    type: ValueType
    updated_at: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    """A namespaced, JSON-file-backed key/value store."""

    def __init__(self, data_dir: str | os.PathLike[str]):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # -- file helpers --------------------------------------------------
    def _path(self, namespace: str) -> Path:
        if not namespace or "/" in namespace or "\\" in namespace or namespace.startswith("."):
            raise InvalidNamespaceError(f"Invalid namespace: {namespace!r}")
        if namespace in RESERVED_NAMESPACES:
            raise InvalidNamespaceError(f"Reserved namespace name: {namespace!r}")
        return self.data_dir / f"{namespace}.json"

    @staticmethod
    def _check_key(key: str) -> None:
        if not key or not key.strip():
            raise InvalidKeyError("Key must be a non-empty string.")

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

    # -- public API ----------------------------------------------------
    def list_namespaces(self) -> list[str]:
        return sorted(p.stem for p in self.data_dir.glob("*.json"))

    def list_entries(self, namespace: str) -> list[Entry]:
        with self._lock:
            data = self._load(namespace)
        return [self._to_entry(k, v) for k, v in sorted(data.items())]

    def get(self, namespace: str, key: str) -> Entry:
        with self._lock:
            data = self._load(namespace)
        if key not in data:
            raise KeyNotFoundError(key)
        return self._to_entry(key, data[key])

    def _resolve_type(self, value: Any, value_type: ValueType | str | None) -> ValueType:
        if value_type is None:
            return infer_type(value)
        vt = ValueType(value_type)
        validate(value, vt)
        return vt

    def set(
        self,
        namespace: str,
        key: str,
        value: Any,
        value_type: ValueType | str | None = None,
    ) -> Entry:
        """Create or update (upsert)."""
        self._check_key(key)
        vt = self._resolve_type(value, value_type)
        with self._lock:
            data = self._load(namespace)
            record = {"value": value, "type": vt.value, "updated_at": _now()}
            data[key] = record
            self._dump(namespace, data)
        return self._to_entry(key, record)

    def create(
        self,
        namespace: str,
        key: str,
        value: Any,
        value_type: ValueType | str | None = None,
    ) -> Entry:
        """Create a new key. Raises :class:`KeyExistsError` if it exists."""
        self._check_key(key)
        vt = self._resolve_type(value, value_type)
        with self._lock:
            data = self._load(namespace)
            if key in data:
                raise KeyExistsError(key)
            record = {"value": value, "type": vt.value, "updated_at": _now()}
            data[key] = record
            self._dump(namespace, data)
        return self._to_entry(key, record)

    def update(
        self,
        namespace: str,
        key: str,
        value: Any,
        value_type: ValueType | str | None = None,
    ) -> Entry:
        """Update an existing key. Raises :class:`KeyNotFoundError` if absent."""
        self._check_key(key)
        vt = self._resolve_type(value, value_type)
        with self._lock:
            data = self._load(namespace)
            if key not in data:
                raise KeyNotFoundError(key)
            record = {"value": value, "type": vt.value, "updated_at": _now()}
            data[key] = record
            self._dump(namespace, data)
        return self._to_entry(key, record)

    def delete(self, namespace: str, key: str) -> None:
        with self._lock:
            data = self._load(namespace)
            if key not in data:
                raise KeyNotFoundError(key)
            del data[key]
            self._dump(namespace, data)

    def create_namespace(self, namespace: str) -> bool:
        """Explicitly create an (empty) namespace. Returns False if it existed."""
        with self._lock:
            path = self._path(namespace)
            if path.exists():
                return False
            self._dump(namespace, {})
            return True

    def delete_namespace(self, namespace: str) -> bool:
        """Delete a namespace. Returns True if it existed, False otherwise."""
        with self._lock:
            path = self._path(namespace)
            if not path.exists():
                return False
            path.unlink()
            return True

    @staticmethod
    def _to_entry(key: str, record: dict) -> Entry:
        return Entry(
            key=key,
            value=record["value"],
            type=ValueType(record["type"]),
            updated_at=record.get("updated_at", ""),
        )
