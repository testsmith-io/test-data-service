"""Password hashing with scrypt (Python stdlib — no external dependency).

Unlike API tokens (long, random, high-entropy — a single SHA-256 is fine), user
passwords are human-chosen and low-entropy, so they are hashed with a slow,
memory-hard KDF (scrypt) and a per-password random salt. Stored format:

    scrypt$<n>$<r>$<p>$<salt_hex>$<hash_hex>
"""

from __future__ import annotations

import hashlib
import hmac
import os

_N, _R, _P, _DKLEN = 16384, 8, 1, 32


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        expected = bytes.fromhex(hash_hex)
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False
