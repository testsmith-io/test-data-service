"""Persistent, hashed storage of API keys and their per-namespace grants.

Security notes
--------------
* The plaintext token is shown **once**, at creation time, and never stored.
* On disk we keep only a SHA-256 hash of the token. API tokens are long,
  high-entropy random strings (``secrets.token_urlsafe(32)`` → 256 bits), so a
  single fast hash is appropriate here — unlike human-chosen passwords, they are
  not vulnerable to brute-force/dictionary attacks, so a slow KDF (bcrypt/argon2)
  is unnecessary. An optional server-side pepper (``TDS_KEY_PEPPER``) can be
  mixed in for defence in depth.
* Lookups are by hash, so a leaked keystore file cannot be used to authenticate.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

_PEPPER = os.environ.get("TDS_KEY_PEPPER", "")


def hash_token(token: str) -> str:
    """Hash a token for storage/lookup (optionally peppered via HMAC)."""
    if _PEPPER:
        return hmac.new(_PEPPER.encode(), token.encode(), hashlib.sha256).hexdigest()
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class KeyRecord:
    id: str
    name: str
    token_hash: str
    grants: dict[str, str]
    created_at: str
    prefix: str  # first chars of the plaintext token, for display only
    owner: str | None = None  # username of the account that owns this key, if any

    def public(self) -> dict:
        """Representation safe to return over the API (no hash)."""
        return {
            "id": self.id,
            "name": self.name,
            "grants": self.grants,
            "created_at": self.created_at,
            "prefix": self.prefix,
            "owner": self.owner,
        }


class KeyStore:
    """A JSON-file-backed store of hashed API keys."""

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
    def create(
        self, name: str, grants: dict[str, str], owner: str | None = None
    ) -> tuple[str, KeyRecord]:
        """Mint a new key. Returns ``(plaintext_token, record)``.

        The plaintext token is the only time it is ever available.
        """
        token = generate_token()
        record = KeyRecord(
            id=secrets.token_hex(8),
            name=name,
            token_hash=hash_token(token),
            grants=dict(grants),
            created_at=_now(),
            prefix=token[:6],
            owner=owner,
        )
        with self._lock:
            data = self._load()
            data[record.id] = asdict(record)
            self._dump(data)
        return token, record

    def list(self) -> list[KeyRecord]:
        with self._lock:
            data = self._load()
        records = [KeyRecord(**r) for r in data.values()]
        return sorted(records, key=lambda r: r.created_at)

    def get(self, key_id: str) -> KeyRecord | None:
        with self._lock:
            data = self._load()
        rec = data.get(key_id)
        return KeyRecord(**rec) if rec else None

    def update_grants(self, key_id: str, grants: dict[str, str]) -> KeyRecord | None:
        with self._lock:
            data = self._load()
            if key_id not in data:
                return None
            data[key_id]["grants"] = dict(grants)
            self._dump(data)
            return KeyRecord(**data[key_id])

    def delete(self, key_id: str) -> bool:
        with self._lock:
            data = self._load()
            if key_id not in data:
                return False
            del data[key_id]
            self._dump(data)
            return True

    def resolve_by_hash(self, token_hash: str) -> dict[str, str] | None:
        """Return the grants for a token hash, or None if unknown."""
        with self._lock:
            data = self._load()
        for rec in data.values():
            if hmac.compare_digest(rec["token_hash"], token_hash):
                return rec["grants"]
        return None
