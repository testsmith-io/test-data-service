"""Test Data Service — a disk-backed namespace/table/row store with an API and web UI."""

from .app import create_app
from .config import Config
from .storage import (
    Storage,
    FileStorage,
    StorageBackend,
    open_storage,
    Row,
    NamespaceNotFoundError,
    TableNotFoundError,
    TableExistsError,
    RowNotFoundError,
    InvalidNamespaceError,
    InvalidTableError,
)
from .sqlite_storage import SqliteStorage

__version__ = "0.1.0"
__all__ = [
    "create_app",
    "Config",
    "Storage",
    "FileStorage",
    "SqliteStorage",
    "StorageBackend",
    "open_storage",
    "Row",
    "NamespaceNotFoundError",
    "TableNotFoundError",
    "TableExistsError",
    "RowNotFoundError",
    "InvalidNamespaceError",
    "InvalidTableError",
]
