"""Test Data Service — a disk-backed key/value store with an API and web UI."""

from .app import create_app
from .config import Config
from .storage import (
    Storage,
    Entry,
    KeyNotFoundError,
    KeyExistsError,
    InvalidNamespaceError,
    InvalidKeyError,
)
from .types import ValueType

__version__ = "0.1.0"
__all__ = [
    "create_app",
    "Config",
    "Storage",
    "Entry",
    "KeyNotFoundError",
    "KeyExistsError",
    "InvalidNamespaceError",
    "InvalidKeyError",
    "ValueType",
]
