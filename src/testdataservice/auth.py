"""Authentication and per-namespace authorization.

Two credential types are accepted (see the architecture the project chose):

* **API key** — ``X-API-Key`` header (or ``?api_key=`` query). Resolves to a set
  of per-namespace grants. Used by scripts and machine-to-machine clients.
* **Session cookie** — set when a user logs in through the web UI. Resolves to
  that user account (its grants, admin flag, and username).

When ``auth_enabled`` is False every request is a superuser and no credentials
are required.

Roles per namespace: ``admin`` (read + write / full CRUD) and ``reader`` (read
only). ``is_admin`` on an account (or a key granting admin on ``*``) is a global
admin who may manage namespaces, accounts, and keys.
"""

from __future__ import annotations

from fastapi import Cookie, Depends, Header, HTTPException, Query, status

from .config import WILDCARD, Config

SESSION_COOKIE = "tds_session"

_ROLE_PERMS = {
    "admin": {"read", "write"},
    "reader": {"read"},
}


class Principal:
    def __init__(
        self,
        grants: dict[str, str] | None = None,
        *,
        superuser: bool = False,
        admin: bool = False,
        username: str | None = None,
    ):
        self.grants = grants or {}
        self.superuser = superuser
        self.admin = admin
        self.username = username

    def role_for(self, namespace: str) -> str | None:
        if self.superuser or self.admin:
            return "admin"
        if namespace in self.grants:
            return self.grants[namespace]
        return self.grants.get(WILDCARD)

    def can(self, perm: str, namespace: str) -> bool:
        role = self.role_for(namespace)
        return role is not None and perm in _ROLE_PERMS.get(role, set())

    def can_see(self, namespace: str) -> bool:
        return self.role_for(namespace) is not None

    def is_global_admin(self) -> bool:
        return self.superuser or self.admin or self.grants.get(WILDCARD) == "admin"

    def describe(self) -> str:
        if self.superuser or self.admin:
            return "admin (all namespaces)"
        if not self.grants:
            return "no access"
        parts = []
        for ns, role in sorted(self.grants.items()):
            parts.append(f"{role} (all)" if ns == WILDCARD else f"{role}:{ns}")
        return ", ".join(parts)


def build_dependencies(config: Config, resolve_key, resolve_session):
    """Return FastAPI dependencies.

    ``resolve_key(token) -> Principal | None`` maps an API key to a principal.
    ``resolve_session(token) -> Principal | None`` maps a session cookie to a
    principal.
    """

    def _lookup(
        x_api_key: str | None, api_key: str | None, session: str | None
    ) -> Principal | None:
        if not config.auth_enabled:
            return Principal(superuser=True)
        token = x_api_key or api_key
        if token:
            principal = resolve_key(token)
            if principal is not None:
                return principal
        if session:
            principal = resolve_session(session)
            if principal is not None:
                return principal
        return None

    def current_principal(
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        api_key: str | None = Query(default=None),
        session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> Principal:
        principal = _lookup(x_api_key, api_key, session)
        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required (API key or login).",
                headers={"WWW-Authenticate": "ApiKey"},
            )
        return principal

    def optional_principal(
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        api_key: str | None = Query(default=None),
        session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> Principal | None:
        return _lookup(x_api_key, api_key, session)

    def require(perm: str):
        def checker(
            namespace: str,
            principal: Principal = Depends(current_principal),
        ) -> Principal:
            if not principal.can(perm, namespace):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Not permitted to {perm} namespace {namespace!r}.",
                )
            return principal

        return checker

    def require_admin(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.is_global_admin():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Global admin privileges required.",
            )
        return principal

    return current_principal, optional_principal, require, require_admin
