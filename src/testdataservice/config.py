"""Runtime configuration for the Test Data Service.

Everything is driven by environment variables so the service can be configured
without code changes:

    TDS_DATA_DIR      Directory where namespace files live. Default: ./tds-data
    TDS_AUTH_ENABLED  "1"/"true" to turn on role-based access. Default: off.
    TDS_ADMIN_KEY     Bootstrap admin token (admin on all namespaces). Use it to
                      sign in and mint further keys via the admin API. Only its
                      hash is kept in memory; it is never written to disk.
    TDS_API_KEYS      Additional bootstrap API keys (hashed in memory on load).
                      Prefer minting keys at runtime via the admin API, which
                      stores them hashed on disk.
    TDS_SECRET        Secret used to sign web-UI session cookies. If unset, a
                      random secret is generated and persisted under the data dir.

Settings may instead be placed in a YAML file in the project root (``tds.yml``
or ``tds.yaml``), e.g.::

    data_dir: ./tds-data
    auth: true
    host: 127.0.0.1
    port: 8000

Environment variables override values from the file.

Access control is **per namespace**. Each API key is granted a role on one or
more namespaces (or on ``*`` meaning every namespace). Roles are ``admin``
(read + write) and ``reader`` (read only).

``TDS_API_KEYS`` format — keys separated by ``;``, grants within a key by ``,``:

    <api-key>: <grant>, <grant>, ...

where each grant is ``<namespace>=<role>`` or a bare ``<role>`` (shorthand for
``*=<role>``, i.e. the role on all namespaces). Example:

    TDS_API_KEYS="root:admin; team-a:projects=admin,shared=reader; ro:reader"

  * root     -> admin on every namespace
  * team-a   -> admin on "projects", reader on "shared", no access elsewhere
  * ro       -> reader on every namespace
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROLES = {"admin", "reader"}
WILDCARD = "*"

# YAML config files looked for in the project root, in order.
CONFIG_FILENAMES = ("tds.yml", "tds.yaml")


def _as_bool(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_grants(grants_str: str) -> dict[str, str]:
    """Parse the grant list for one key into ``{namespace: role}``."""
    grants: dict[str, str] = {}
    for grant in grants_str.split(","):
        grant = grant.strip()
        if not grant:
            continue
        if "=" in grant:
            namespace, _, role = grant.partition("=")
            namespace = namespace.strip() or WILDCARD
            role = role.strip()
        else:
            namespace, role = WILDCARD, grant
        if role not in ROLES:
            raise ValueError(f"Unknown role {role!r} for an API key.")
        grants[namespace] = role
    return grants


def _parse_keys(raw: str | None) -> dict[str, dict[str, str]]:
    keys: dict[str, dict[str, str]] = {}
    for entry in (raw or "").split(";"):
        entry = entry.strip()
        if not entry:
            continue
        api_key, _, grants_str = entry.partition(":")
        api_key = api_key.strip()
        if not api_key:
            continue
        keys[api_key] = _parse_grants(grants_str)
    return keys


def _find_config_file(start: str | os.PathLike[str] | None = None) -> Path | None:
    base = Path(start or os.getcwd())
    for name in CONFIG_FILENAMES:
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


@dataclass
class Config:
    data_dir: str = "./tds-data"
    auth_enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8000
    # Storage backend: "sqlite" (default) or "file".
    storage: str = "sqlite"
    # SQLite DB path; defaults to <data_dir>/tds.db when unset.
    db_path: str | None = None
    # Bootstrap keys (plaintext here, hashed in memory at startup): api_key -> grants
    api_keys: dict[str, dict[str, str]] = field(default_factory=dict)
    # Bootstrap admin token (admin on all namespaces), if set.
    admin_key: str | None = None
    # Secret for signing session cookies (generated/persisted if unset).
    secret: str | None = None

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            data_dir=os.environ.get("TDS_DATA_DIR", "./tds-data"),
            auth_enabled=_as_bool(os.environ.get("TDS_AUTH_ENABLED")),
            api_keys=_parse_keys(os.environ.get("TDS_API_KEYS")),
            admin_key=os.environ.get("TDS_ADMIN_KEY") or None,
            secret=os.environ.get("TDS_SECRET") or None,
            storage=os.environ.get("TDS_STORAGE", "sqlite"),
            db_path=os.environ.get("TDS_DB_PATH") or None,
        )

    @classmethod
    def load(cls, config_file: str | os.PathLike[str] | None = None) -> "Config":
        """Load config from a YAML file (if present) then overlay environment.

        If ``config_file`` is None, looks for ``tds.yml``/``tds.yaml`` in the
        current working directory. Environment variables take precedence.
        """
        cfg = cls()
        path = Path(config_file) if config_file else _find_config_file()
        if path and path.is_file():
            import yaml

            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ValueError(f"Config file {path} must contain a mapping.")
            cfg.data_dir = str(data.get("data_dir", cfg.data_dir))
            cfg.auth_enabled = bool(data.get("auth", cfg.auth_enabled))
            cfg.host = str(data.get("host", cfg.host))
            cfg.port = int(data.get("port", cfg.port))
            cfg.admin_key = data.get("admin_key") or cfg.admin_key
            cfg.secret = data.get("secret") or cfg.secret
            cfg.storage = str(data.get("storage", cfg.storage))
            cfg.db_path = data.get("db_path") or cfg.db_path
            if isinstance(data.get("api_keys"), str):
                cfg.api_keys = _parse_keys(data["api_keys"])

        # Environment overrides file.
        if "TDS_DATA_DIR" in os.environ:
            cfg.data_dir = os.environ["TDS_DATA_DIR"]
        if "TDS_AUTH_ENABLED" in os.environ:
            cfg.auth_enabled = _as_bool(os.environ["TDS_AUTH_ENABLED"])
        if "TDS_API_KEYS" in os.environ:
            cfg.api_keys = _parse_keys(os.environ["TDS_API_KEYS"])
        if os.environ.get("TDS_ADMIN_KEY"):
            cfg.admin_key = os.environ["TDS_ADMIN_KEY"]
        if os.environ.get("TDS_SECRET"):
            cfg.secret = os.environ["TDS_SECRET"]
        if "TDS_STORAGE" in os.environ:
            cfg.storage = os.environ["TDS_STORAGE"]
        if os.environ.get("TDS_DB_PATH"):
            cfg.db_path = os.environ["TDS_DB_PATH"]
        return cfg
