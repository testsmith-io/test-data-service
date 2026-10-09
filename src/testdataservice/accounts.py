"""Persistent user accounts (admin + users). Passwords are hashed, never stored
in plaintext (see :mod:`testdataservice.passwords`).

An account has per-namespace grants ({namespace: role}) exactly like an API key,
so a user's API keys can inherit the account's permissions. ``is_admin`` marks a
global admin who can manage namespaces, accounts, and keys.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

from .passwords import hash_password, verify_password

DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Account:
    username: str
    password_hash: str
    is_admin: bool = False
    grants: dict[str, str] = field(default_factory=dict)  # namespace -> role
    must_change_password: bool = False
    created_at: str = ""

    def public(self) -> dict:
        """Representation safe to return over the API (no password hash)."""
        return {
            "username": self.username,
            "is_admin": self.is_admin,
            "grants": self.grants,
            "must_change_password": self.must_change_password,
            "created_at": self.created_at,
        }


class UsernameExistsError(ValueError):
    pass


class AccountStore:
    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # -- file helpers --------------------------------------------------
    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        with self.path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def _dump(self, data: dict[str, dict]) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # -- public API ----------------------------------------------------
    def list(self) -> list["Account"]:
        with self._lock:
            data = self._load()
        return sorted((Account(**a) for a in data.values()), key=lambda a: a.username)

    def get(self, username: str) -> "Account | None":
        with self._lock:
            data = self._load()
        rec = data.get(username)
        return Account(**rec) if rec else None

    def exists(self) -> bool:
        with self._lock:
            return bool(self._load())

    def create(
        self,
        username: str,
        password: str,
        *,
        is_admin: bool = False,
        grants: dict[str, str] | None = None,
        must_change_password: bool = False,
    ) -> "Account":
        username = username.strip()
        if not username:
            raise ValueError("Username must be non-empty.")
        if not password:
            raise ValueError("Password must be non-empty.")
        with self._lock:
            data = self._load()
            if username in data:
                raise UsernameExistsError(f"Account {username!r} already exists.")
            acct = Account(
                username=username,
                password_hash=hash_password(password),
                is_admin=is_admin,
                grants=dict(grants or {}),
                must_change_password=must_change_password,
                created_at=_now(),
            )
            data[username] = asdict(acct)
            self._dump(data)
        return acct

    def set_password(self, username: str, new_password: str) -> bool:
        if not new_password:
            raise ValueError("Password must be non-empty.")
        with self._lock:
            data = self._load()
            if username not in data:
                return False
            data[username]["password_hash"] = hash_password(new_password)
            data[username]["must_change_password"] = False
            self._dump(data)
        return True

    def update_grants(self, username: str, grants: dict[str, str]) -> "Account | None":
        with self._lock:
            data = self._load()
            if username not in data:
                return None
            data[username]["grants"] = dict(grants)
            self._dump(data)
            return Account(**data[username])

    def delete(self, username: str) -> bool:
        with self._lock:
            data = self._load()
            if username not in data:
                return False
            del data[username]
            self._dump(data)
        return True

    def verify(self, username: str, password: str) -> "Account | None":
        acct = self.get(username)
        if acct and verify_password(password, acct.password_hash):
            return acct
        return None

    def ensure_default_admin(self) -> None:
        """Seed the default admin/admin account (must change on first login)."""
        with self._lock:
            if self._load():
                return
            self.create(
                DEFAULT_ADMIN_USER,
                DEFAULT_ADMIN_PASSWORD,
                is_admin=True,
                must_change_password=True,
            )
