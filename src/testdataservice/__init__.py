"""Test Data Service — a disk-backed namespace/table/row store with an API and web UI."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

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

try:
    __version__ = _pkg_version("test-data-service")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0.7.0"
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
