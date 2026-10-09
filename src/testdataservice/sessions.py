"""Stateless signed session tokens for the web UI (HMAC-SHA256, stdlib).

A token is ``<base64url(payload)>.<base64url(hmac)>`` where payload is
``{"u": username, "exp": unix_ts}``. No server-side session storage is needed;
the signature and expiry are verified on each request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue(secret: str, username: str, ttl: int = 86400) -> str:
    payload = {"u": username, "exp": int(time.time()) + ttl}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64e(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify(secret: str, token: str) -> str | None:
    """Return the username if the token is valid and unexpired, else None."""
    try:
        body, sig = token.split(".")
        expected = _b64e(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64d(body))
        if payload.get("exp", 0) < time.time():
            return None
        return payload.get("u")
    except (ValueError, TypeError):
        return None
