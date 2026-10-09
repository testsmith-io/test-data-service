"""FastAPI application exposing the Test Data Service.

Provides:
  * A JSON REST API under ``/api``.
  * A minimal web interface at ``/``.
  * Role-based access control that can be toggled via config.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import secrets

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, field_validator

from . import sessions
from .accounts import AccountStore, UsernameExistsError
from .auth import SESSION_COOKIE, Principal, build_dependencies
from .config import Config
from .keystore import KeyStore, hash_token
from .storage import (
    InvalidNamespaceError,
    KeyExistsError,
    KeyNotFoundError,
    Storage,
)
from .types import ValueType

ROLES = {"admin", "reader"}


def _validate_grants(grants: dict[str, str]) -> dict[str, str]:
    if not grants:
        raise ValueError("At least one namespace grant is required.")
    for ns, role in grants.items():
        if role not in ROLES:
            raise ValueError(f"Unknown role {role!r}; expected one of {sorted(ROLES)}.")
        if not ns or not str(ns).strip():
            raise ValueError("Grant namespace must be non-empty (use '*' for all).")
    return grants


_WEB_DIR = Path(__file__).parent / "web"


class EntryIn(BaseModel):
    """Body for PUT (update) — the key comes from the URL."""

    value: Any
    type: ValueType | None = None


class EntryCreate(BaseModel):
    """Body for POST (create) — the key is supplied in the body."""

    key: str
    value: Any
    type: ValueType | None = None

    @field_validator("key")
    @classmethod
    def _key_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Key must be a non-empty string.")
        return v


class ApiKeyCreate(BaseModel):
    """Admin request to mint an API key with per-namespace grants."""

    name: str
    grants: dict[str, str]  # {namespace or "*": "admin"|"reader"}


class ApiKeyGrants(BaseModel):
    grants: dict[str, str]


class Login(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class NamespaceCreate(BaseModel):
    name: str


class AccountCreate(BaseModel):
    """Admin request to create a user account scoped to namespaces."""

    username: str
    password: str
    grants: dict[str, str]  # {namespace: "admin"|"reader"}
    is_admin: bool = False


class KeyName(BaseModel):
    name: str = "api-key"


def create_app(config: Config | None = None) -> FastAPI:
    # Config.load() reads tds.yml/tds.yaml from the cwd, then overlays env vars.
    config = config or Config.load()
    auth_dir = Path(config.data_dir) / ".auth"
    storage = Storage(config.data_dir)
    keystore = KeyStore(auth_dir / "keys.json")
    accounts = AccountStore(auth_dir / "accounts.json")

    # Secret for signing session cookies: config/env, else generate + persist.
    secret = config.secret
    if not secret:
        secret_file = auth_dir / "secret"
        auth_dir.mkdir(parents=True, exist_ok=True)
        if secret_file.exists():
            secret = secret_file.read_text(encoding="utf-8").strip()
        else:
            secret = secrets.token_urlsafe(32)
            secret_file.write_text(secret, encoding="utf-8")

    # Bootstrap keys are hashed in memory at startup; plaintext is discarded.
    bootstrap: dict[str, dict[str, str]] = {
        hash_token(tok): grants for tok, grants in config.api_keys.items()
    }
    if config.admin_key:
        bootstrap[hash_token(config.admin_key)] = {"*": "admin"}

    # When auth is on, make sure the default admin account exists (admin/admin,
    # must change password on first login).
    if config.auth_enabled:
        accounts.ensure_default_admin()

    def resolve_key(token: str) -> Principal | None:
        """Map an API key to a principal (bootstrap first, then keystore)."""
        h = hash_token(token)
        grants = bootstrap.get(h)
        if grants is None:
            grants = keystore.resolve_by_hash(h)
        if grants is None:
            return None
        return Principal(grants)

    def resolve_session(token: str) -> Principal | None:
        """Map a session cookie to the logged-in account's principal."""
        username = sessions.verify(secret, token)
        if not username:
            return None
        acct = accounts.get(username)
        if acct is None:
            return None
        return Principal(acct.grants, admin=acct.is_admin, username=username)

    current_principal, optional_principal, require, require_admin = build_dependencies(
        config, resolve_key, resolve_session
    )

    description = (
        "A disk-backed key/value store for test data, grouped into namespaces.\n\n"
        "Values are one of four types: `string`, `boolean`, `number`, or `array`.\n\n"
    )
    if config.auth_enabled:
        description += (
            "**Authentication is enabled.** Send your API key in the `X-API-Key` "
            "header. `admin` keys may read and write; `reader` keys may only read."
        )
    else:
        description += (
            "**Authentication is disabled** — all endpoints are open. Enable it with "
            "`--auth` (or `TDS_AUTH_ENABLED=1`) to require an `X-API-Key` header."
        )

    app = FastAPI(
        title="Test Data Service",
        version="0.1.0",
        description=description,
        openapi_tags=[
            {"name": "entries", "description": "Read and write key/value entries."},
            {"name": "namespaces", "description": "List, create, and delete namespaces."},
            {"name": "accounts", "description": "Admin: manage user accounts."},
            {"name": "auth", "description": "Log in/out and change your password."},
            {"name": "keys", "description": "Manage API keys (admin, and your own)."},
            {"name": "meta", "description": "Service health and status."},
        ],
    )
    app.state.config = config
    app.state.storage = storage

    jinja_env = Environment(
        loader=FileSystemLoader(str(_WEB_DIR / "templates")),
        autoescape=select_autoescape(["html"]),
        cache_size=0,
    )
    static_dir = _WEB_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ------------------------------------------------------------------ API
    @app.get("/api/health", tags=["meta"], summary="Health check")
    def health() -> dict:
        return {"status": "ok", "auth_enabled": config.auth_enabled}

    @app.get("/api/whoami", tags=["meta"], summary="Describe the caller's access")
    def whoami(principal=Depends(optional_principal)) -> dict:
        if principal is None:
            return {
                "authenticated": False,
                "access": "not signed in",
                "is_admin": False,
                "username": None,
                "must_change_password": False,
            }
        must_change = False
        if principal.username:
            acct = accounts.get(principal.username)
            must_change = bool(acct and acct.must_change_password)
        return {
            "authenticated": True,
            "access": principal.describe(),
            "is_admin": principal.is_global_admin(),
            "username": principal.username,
            "must_change_password": must_change,
        }

    # ------------------------------------------------------------------ Auth (accounts)
    @app.post("/api/login", tags=["auth"], summary="Log in with username + password")
    def login(body: Login, response: Response) -> dict:
        acct = accounts.verify(body.username, body.password)
        if acct is None:
            raise HTTPException(status_code=401, detail="Invalid username or password.")
        token = sessions.issue(secret, acct.username)
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=86400)
        return {
            "username": acct.username,
            "is_admin": acct.is_admin,
            "must_change_password": acct.must_change_password,
        }

    @app.post("/api/logout", tags=["auth"], summary="Log out (clear session)")
    def logout(response: Response) -> dict:
        response.delete_cookie(SESSION_COOKIE)
        return {"ok": True}

    @app.post("/api/password", tags=["auth"], summary="Change your own password")
    def change_password(body: PasswordChange, principal=Depends(current_principal)) -> dict:
        if not principal.username:
            raise HTTPException(
                status_code=400,
                detail="Password change applies to user accounts, not API keys.",
            )
        if accounts.verify(principal.username, body.current_password) is None:
            raise HTTPException(status_code=403, detail="Current password is incorrect.")
        if len(body.new_password) < 4:
            raise HTTPException(status_code=422, detail="New password is too short.")
        accounts.set_password(principal.username, body.new_password)
        return {"ok": True}

    # ------------------------------------------------------------------ My API keys
    @app.get("/api/me/keys", tags=["keys"], summary="List your own API keys")
    def list_my_keys(principal=Depends(current_principal)) -> dict:
        if not principal.username:
            raise HTTPException(status_code=400, detail="Only user accounts own keys.")
        mine = [r.public() for r in keystore.list() if r.owner == principal.username]
        return {"keys": mine}

    @app.post(
        "/api/me/keys",
        status_code=201,
        tags=["keys"],
        summary="Mint an API key inheriting your permissions",
    )
    def create_my_key(body: KeyName, principal=Depends(current_principal)) -> dict:
        if not principal.username:
            raise HTTPException(status_code=400, detail="Only user accounts own keys.")
        acct = accounts.get(principal.username)
        grants = {"*": "admin"} if acct and acct.is_admin else dict(acct.grants if acct else {})
        if not grants:
            raise HTTPException(status_code=422, detail="Your account has no namespace grants.")
        token, record = keystore.create(body.name, grants, owner=principal.username)
        return {"token": token, **record.public()}

    @app.delete("/api/me/keys/{key_id}", tags=["keys"], summary="Revoke one of your API keys")
    def delete_my_key(key_id: str, principal=Depends(current_principal)) -> dict:
        rec = keystore.get(key_id)
        if rec is None or rec.owner != principal.username:
            raise HTTPException(status_code=404, detail="API key not found.")
        keystore.delete(key_id)
        return {"revoked": key_id}

    @app.get(
        "/api/namespaces",
        tags=["namespaces"],
        summary="List namespaces you can access",
    )
    def list_namespaces(principal=Depends(current_principal)) -> dict:
        names = [n for n in storage.list_namespaces() if principal.can_see(n)]
        return {"namespaces": names}

    @app.post(
        "/api/namespaces",
        dependencies=[Depends(require_admin)],
        status_code=201,
        tags=["namespaces"],
        summary="Create a namespace (admin)",
    )
    def create_namespace(body: NamespaceCreate) -> dict:
        try:
            created = storage.create_namespace(body.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not created:
            raise HTTPException(status_code=409, detail="Namespace already exists.")
        return {"created": body.name}

    # -- User accounts (admin) --------------------------------------------------
    @app.get(
        "/api/accounts",
        dependencies=[Depends(require_admin)],
        tags=["accounts"],
        summary="List user accounts (admin)",
    )
    def list_accounts() -> dict:
        return {"accounts": [a.public() for a in accounts.list()]}

    @app.post(
        "/api/accounts",
        dependencies=[Depends(require_admin)],
        status_code=201,
        tags=["accounts"],
        summary="Create a user account scoped to namespaces (admin)",
    )
    def create_account(body: AccountCreate) -> dict:
        if not body.is_admin:
            try:
                _validate_grants(body.grants)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            # A user may only be granted access to namespaces that exist.
            existing = set(storage.list_namespaces())
            missing = [ns for ns in body.grants if ns != "*" and ns not in existing]
            if missing:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown namespace(s): {', '.join(missing)}. Create them first.",
                )
        try:
            acct = accounts.create(
                body.username,
                body.password,
                is_admin=body.is_admin,
                grants={} if body.is_admin else body.grants,
                must_change_password=False,
            )
        except UsernameExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return acct.public()

    @app.delete(
        "/api/accounts/{username}",
        dependencies=[Depends(require_admin)],
        tags=["accounts"],
        summary="Delete a user account (admin)",
    )
    def delete_account(username: str, principal=Depends(require_admin)) -> dict:
        if username == principal.username:
            raise HTTPException(status_code=400, detail="You cannot delete your own account.")
        if not accounts.delete(username):
            raise HTTPException(status_code=404, detail="Account not found.")
        return {"deleted": username}

    # -- API keys (admin) -------------------------------------------------------
    # Registered before the generic ``/api/{namespace}`` routes so the literal
    # ``keys`` path is not captured as a namespace.
    @app.get(
        "/api/keys",
        dependencies=[Depends(require_admin)],
        tags=["keys"],
        summary="List API keys (admin)",
    )
    def list_keys() -> dict:
        return {"keys": [r.public() for r in keystore.list()]}

    @app.post(
        "/api/keys",
        dependencies=[Depends(require_admin)],
        status_code=201,
        tags=["keys"],
        summary="Create an API key with per-namespace grants (admin)",
    )
    def create_key(body: ApiKeyCreate) -> dict:
        try:
            _validate_grants(body.grants)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        token, record = keystore.create(body.name, body.grants)
        # The plaintext token is returned exactly once, here.
        return {"token": token, **record.public()}

    @app.put(
        "/api/keys/{key_id}",
        dependencies=[Depends(require_admin)],
        tags=["keys"],
        summary="Update an API key's grants (admin)",
    )
    def update_key(key_id: str, body: ApiKeyGrants) -> dict:
        try:
            _validate_grants(body.grants)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        record = keystore.update_grants(key_id, body.grants)
        if record is None:
            raise HTTPException(status_code=404, detail="API key not found.")
        return record.public()

    @app.delete(
        "/api/keys/{key_id}",
        dependencies=[Depends(require_admin)],
        tags=["keys"],
        summary="Revoke an API key (admin)",
    )
    def delete_key(key_id: str) -> dict:
        if not keystore.delete(key_id):
            raise HTTPException(status_code=404, detail="API key not found.")
        return {"revoked": key_id}

    def _invalid_namespace(exc: ValueError) -> HTTPException:
        return HTTPException(status_code=400, detail=str(exc))

    @app.get(
        "/api/{namespace}",
        dependencies=[Depends(require("read"))],
        tags=["entries"],
        summary="List entries in a namespace",
    )
    def list_entries(namespace: str) -> dict:
        try:
            return {"entries": [e.to_dict() for e in storage.list_entries(namespace)]}
        except ValueError as exc:
            raise _invalid_namespace(exc)

    @app.get(
        "/api/{namespace}/{key}",
        dependencies=[Depends(require("read"))],
        tags=["entries"],
        summary="Get a single entry",
    )
    def get_entry(namespace: str, key: str) -> dict:
        try:
            return storage.get(namespace, key).to_dict()
        except KeyNotFoundError:
            raise HTTPException(status_code=404, detail="Key not found.")
        except ValueError as exc:
            raise _invalid_namespace(exc)

    @app.post(
        "/api/{namespace}",
        dependencies=[Depends(require("write"))],
        status_code=201,
        tags=["entries"],
        summary="Create a new entry in a namespace",
    )
    def create_entry(namespace: str, body: EntryCreate, response: Response) -> dict:
        try:
            entry = storage.create(namespace, body.key, body.value, body.type)
        except KeyExistsError:
            raise HTTPException(
                status_code=409,
                detail=f"Key {body.key!r} already exists; use PUT to update it.",
            )
        except InvalidNamespaceError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except ValueError as exc:  # bad value type / blank key
            raise HTTPException(status_code=422, detail=str(exc))
        # 201 Created points at the new resource's URI.
        response.headers["Location"] = f"/api/{namespace}/{entry.key}"
        return entry.to_dict()

    @app.put(
        "/api/{namespace}/{key}",
        dependencies=[Depends(require("write"))],
        tags=["entries"],
        summary="Update an existing entry",
    )
    def update_entry(namespace: str, key: str, body: EntryIn) -> dict:
        try:
            entry = storage.update(namespace, key, body.value, body.type)
        except KeyNotFoundError:
            raise HTTPException(
                status_code=404,
                detail=f"Key {key!r} not found; use POST to create it.",
            )
        except InvalidNamespaceError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except ValueError as exc:  # bad value type
            raise HTTPException(status_code=422, detail=str(exc))
        return entry.to_dict()

    @app.delete(
        "/api/{namespace}/{key}",
        dependencies=[Depends(require("write"))],
        tags=["entries"],
        summary="Delete an entry",
    )
    def delete_entry(namespace: str, key: str) -> dict:
        try:
            storage.delete(namespace, key)
        except KeyNotFoundError:
            raise HTTPException(status_code=404, detail="Key not found.")
        except ValueError as exc:
            raise _invalid_namespace(exc)
        return {"deleted": key}

    @app.delete(
        "/api/{namespace}",
        dependencies=[Depends(require("write"))],
        tags=["namespaces"],
        summary="Delete an entire namespace",
    )
    def delete_namespace(namespace: str) -> dict:
        try:
            existed = storage.delete_namespace(namespace)
        except ValueError as exc:
            raise _invalid_namespace(exc)
        if not existed:
            raise HTTPException(status_code=404, detail="Namespace not found.")
        return {"deleted": namespace}

    # ------------------------------------------------------------------ Web UI
    @app.get("/", response_class=HTMLResponse)
    def index():
        # The page is a shell; JavaScript drives login/state via /api/whoami.
        html = jinja_env.get_template("index.html").render(
            auth_enabled=config.auth_enabled,
            value_types=[t.value for t in ValueType],
        )
        return HTMLResponse(html)

    return app


def __getattr__(name: str):
    # Lazily build the app only when actually requested (e.g. uvicorn's
    # ``testdataservice.app:app`` import string), so importing the package has
    # no filesystem side effects.
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
