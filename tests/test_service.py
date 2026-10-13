import pytest
from fastapi.testclient import TestClient

from testdataservice import FileStorage, SqliteStorage
from testdataservice.app import create_app
from testdataservice.config import Config
from testdataservice.storage import (
    TableExistsError,
    TableNotFoundError,
    RowNotFoundError,
)


# ------------------------------------------------------------- storage (both backends)
@pytest.fixture(params=["file", "sqlite"])
def make_storage(request, tmp_path):
    """Return a factory that (re)opens a backend at a stable location."""
    if request.param == "file":
        return lambda: FileStorage(tmp_path)
    db = tmp_path / "db.sqlite"
    return lambda: SqliteStorage(db)


def test_storage_tables_and_dynamic_rows(make_storage):
    s = make_storage()
    s.create_namespace("ns")
    s.create_table("ns", "users")
    assert s.list_tables("ns") == ["users"]

    a = s.create_row("ns", "users", {"name": "alice", "age": 30, "active": True})
    s.create_row("ns", "users", {"name": "bob", "email": "b@x.io"})

    rows = s.list_rows("ns", "users")
    assert len(rows) == 2
    # Columns are the union of fields across rows (dynamic).
    assert set(s.columns("ns", "users")) == {"name", "age", "active", "email"}
    assert s.get_row("ns", "users", a.id).data["name"] == "alice"


def test_storage_row_update_and_delete(make_storage):
    s = make_storage()
    s.create_table("ns", "t")
    r = s.create_row("ns", "t", {"x": 1})
    assert s.update_row("ns", "t", r.id, {"x": 2, "y": 3}).data == {"x": 2, "y": 3}
    s.delete_row("ns", "t", r.id)
    with pytest.raises(RowNotFoundError):
        s.get_row("ns", "t", r.id)


def test_storage_table_errors(make_storage):
    s = make_storage()
    s.create_table("ns", "t")
    with pytest.raises(TableExistsError):
        s.create_table("ns", "t")
    with pytest.raises(TableNotFoundError):
        s.list_rows("ns", "missing")


def test_storage_persists(make_storage):
    make_storage().create_table("ns", "t")
    rid = make_storage().create_row("ns", "t", {"k": "v"}).id
    assert make_storage().get_row("ns", "t", rid).data == {"k": "v"}


def test_storage_rejects_reserved_namespace(make_storage):
    with pytest.raises(ValueError):
        make_storage().create_table("namespaces", "t")


def test_storage_designed_columns_persist_without_rows(make_storage):
    make_storage().create_table("ns", "t")
    make_storage().set_columns("ns", "t", ["name", "age"])
    # Designed columns persist with zero rows, and show up as effective columns.
    assert make_storage().get_columns("ns", "t") == ["name", "age"]
    assert make_storage().columns("ns", "t") == ["name", "age"]
    # A row with an extra field adds a column on top of the designed ones.
    make_storage().create_row("ns", "t", {"name": "a", "extra": 1})
    assert make_storage().columns("ns", "t") == ["name", "age", "extra"]


# ------------------------------------------------------------- config parsing
def test_config_parses_namespace_scoped_keys():
    from testdataservice.config import _parse_keys

    keys = _parse_keys("root:admin; team-a:projects=admin,shared=reader; ro:reader")
    assert keys["root"] == {"*": "admin"}
    assert keys["team-a"] == {"projects": "admin", "shared": "reader"}
    assert keys["ro"] == {"*": "reader"}


def test_config_rejects_unknown_role():
    from testdataservice.config import _parse_keys

    with pytest.raises(ValueError):
        _parse_keys("k:projects=superuser")


# ------------------------------------------------------------- API (no auth)
@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(Config(data_dir=str(tmp_path), auth_enabled=False)))


def test_api_namespace_table_row_lifecycle(client):
    assert client.post("/api/namespaces", json={"name": "proj"}).status_code == 201
    assert client.post("/api/proj", json={"name": "users"}).status_code == 201
    assert client.get("/api/proj").json() == {"tables": ["users"]}

    r = client.post("/api/proj/users/rows", json={"name": "alice", "age": 30})
    assert r.status_code == 201
    rid = r.json()["id"]
    assert r.json()["fields"] == {"name": "alice", "age": 30}

    # dynamic columns: second row introduces a new column
    client.post("/api/proj/users/rows", json={"name": "bob", "email": "b@x.io"})
    listing = client.get("/api/proj/users/rows").json()
    assert set(listing["columns"]) == {"name", "age", "email"}
    assert len(listing["rows"]) == 2

    assert client.put(f"/api/proj/users/rows/{rid}", json={"name": "alice2"}).status_code == 200
    assert client.get(f"/api/proj/users/rows/{rid}").json()["fields"] == {"name": "alice2"}
    assert client.delete(f"/api/proj/users/rows/{rid}").status_code == 200
    assert client.get(f"/api/proj/users/rows/{rid}").status_code == 404


def test_api_table_conflict_and_missing(client):
    client.post("/api/proj", json={"name": "t"})
    assert client.post("/api/proj", json={"name": "t"}).status_code == 409
    assert client.get("/api/proj/missing/rows").status_code == 404
    assert client.delete("/api/proj/missing").status_code == 404


def test_api_reserved_namespace_is_400(client):
    assert client.post("/api/namespaces", json={"name": "health"}).status_code == 400


def test_web_ui_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Test Data Service" in r.text


def test_api_works_on_file_backend(tmp_path):
    # The default backend is sqlite (covered by `client`); check file too.
    c = TestClient(create_app(Config(data_dir=str(tmp_path), auth_enabled=False, storage="file")))
    assert c.post("/api/n", json={"name": "t"}).status_code == 201
    assert c.post("/api/n/t/rows", json={"a": 1}).status_code == 201
    assert c.get("/api/n/t/rows").json()["columns"] == ["a"]


def test_api_row_filtering(client):
    client.post("/api/shop", json={"name": "products"})
    client.post("/api/shop/products/rows", json={"status": "ok", "ean": 123, "name": "a"})
    client.post("/api/shop/products/rows", json={"status": "ok", "ean": 999, "name": "b"})
    client.post("/api/shop/products/rows", json={"status": "bad", "ean": 123, "name": "c"})

    def names(**params):
        q = "&".join(f"{k}={v}" for k, v in params.items())
        rows = client.get(f"/api/shop/products/rows?{q}").json()["rows"]
        return sorted(r["fields"]["name"] for r in rows)

    assert names(status="ok") == ["a", "b"]
    assert names(ean="123") == ["a", "c"]  # numeric field matched via string
    assert names(status="ok", ean="123") == ["a"]  # multiple conditions AND
    assert names(status="missing") == []


def test_api_design_columns(client):
    client.post("/api/n", json={"name": "t"})
    r = client.put("/api/n/t", json={"columns": ["name", "age", "name"]})  # dupe dropped
    assert r.status_code == 200 and r.json()["columns"] == ["name", "age"]
    # effective columns include the designed ones even with no rows
    listing = client.get("/api/n/t/rows").json()
    assert listing["defined"] == ["name", "age"]
    assert listing["columns"] == ["name", "age"]


def test_sqlite_db_created_in_data_dir(tmp_path):
    import os

    create_app(Config(data_dir=str(tmp_path), auth_enabled=False, storage="sqlite"))
    assert os.path.exists(tmp_path / "tds.db")


# ------------------------------------------------------------- API (auth on)
@pytest.fixture
def auth_client(tmp_path):
    cfg = Config(
        data_dir=str(tmp_path),
        auth_enabled=True,
        api_keys={
            "root-key": {"*": "admin"},
            "global-read": {"*": "reader"},
            "team-key": {"alpha": "admin", "beta": "reader"},
        },
    )
    return TestClient(create_app(cfg))


def test_auth_requires_key(auth_client):
    assert auth_client.get("/api/namespaces").status_code == 401


def test_auth_global_reader_cannot_write(auth_client):
    h = {"X-API-Key": "global-read"}
    assert auth_client.get("/api/namespaces", headers=h).status_code == 200
    assert auth_client.post("/api/alpha", json={"name": "t"}, headers=h).status_code == 403


def test_auth_namespace_scoped(auth_client):
    h = {"X-API-Key": "team-key"}
    # admin on alpha -> can create a table and rows
    assert auth_client.post("/api/alpha", json={"name": "t"}, headers=h).status_code == 201
    assert auth_client.post("/api/alpha/t/rows", json={"x": 1}, headers=h).status_code == 201
    # reader on beta -> can list, cannot write
    assert auth_client.get("/api/beta", headers=h).status_code == 200
    assert auth_client.post("/api/beta", json={"name": "t"}, headers=h).status_code == 403
    # no grant on gamma -> forbidden
    assert auth_client.get("/api/gamma", headers=h).status_code == 403


def test_auth_namespace_list_is_filtered(auth_client):
    root = {"X-API-Key": "root-key"}
    auth_client.post("/api/namespaces", json={"name": "alpha"}, headers=root)
    auth_client.post("/api/namespaces", json={"name": "secret"}, headers=root)
    seen = auth_client.get("/api/namespaces", headers={"X-API-Key": "team-key"}).json()[
        "namespaces"
    ]
    assert "alpha" in seen
    assert "secret" not in seen


def test_whoami_describes_access(auth_client):
    r = auth_client.get("/api/whoami", headers={"X-API-Key": "team-key"})
    assert r.json()["authenticated"] is True
    assert "alpha" in r.json()["access"]
    assert r.json()["is_admin"] is False
    assert auth_client.get("/api/whoami", headers={"X-API-Key": "root-key"}).json()["is_admin"]


# ------------------------------------------------------------- keystore (hashing)
def test_keystore_hashes_token_not_plaintext(tmp_path):
    from testdataservice.keystore import KeyStore, hash_token

    ks = KeyStore(tmp_path / "keys.json")
    token, record = ks.create("ci", {"projects": "admin"})
    raw = (tmp_path / "keys.json").read_text()
    assert token not in raw
    assert hash_token(token) in raw
    assert ks.resolve_by_hash(hash_token(token)) == {"projects": "admin"}
    assert ks.resolve_by_hash(hash_token("wrong")) is None


# ------------------------------------------------------------- admin key management API
def test_admin_can_mint_key_and_it_works(auth_client):
    admin = {"X-API-Key": "root-key"}
    r = auth_client.post(
        "/api/keys", json={"name": "qa", "grants": {"alpha": "reader"}}, headers=admin
    )
    assert r.status_code == 201
    token = r.json()["token"]

    h = {"X-API-Key": token}
    auth_client.post("/api/alpha", json={"name": "t"}, headers=admin)  # admin makes a table
    assert auth_client.get("/api/alpha", headers=h).status_code == 200  # reader can list
    assert auth_client.post("/api/alpha", json={"name": "t2"}, headers=h).status_code == 403
    assert auth_client.get("/api/beta", headers=h).status_code == 403


def test_non_admin_cannot_manage_keys(auth_client):
    h = {"X-API-Key": "team-key"}
    assert auth_client.get("/api/keys", headers=h).status_code == 403
    assert (
        auth_client.post(
            "/api/keys", json={"name": "x", "grants": {"alpha": "reader"}}, headers=h
        ).status_code
        == 403
    )


def test_create_key_rejects_bad_role(auth_client):
    admin = {"X-API-Key": "root-key"}
    r = auth_client.post(
        "/api/keys", json={"name": "x", "grants": {"alpha": "superuser"}}, headers=admin
    )
    assert r.status_code == 422


# ------------------------------------------------------------- passwords & config
def test_password_hashed_not_plaintext(tmp_path):
    from testdataservice.accounts import AccountStore

    store = AccountStore(tmp_path / "accounts.json")
    store.create("bob", "SuperSecret123", grants={"x": "admin"})
    raw = (tmp_path / "accounts.json").read_text()
    assert "SuperSecret123" not in raw
    assert "scrypt$" in raw
    assert store.verify("bob", "SuperSecret123") is not None
    assert store.verify("bob", "wrong") is None


def test_yaml_config_loads(tmp_path, monkeypatch):
    (tmp_path / "tds.yml").write_text("data_dir: ./d\nauth: true\nport: 9001\n")
    monkeypatch.chdir(tmp_path)
    for k in ("TDS_DATA_DIR", "TDS_AUTH_ENABLED", "TDS_API_KEYS"):
        monkeypatch.delenv(k, raising=False)
    cfg = Config.load()
    assert cfg.auth_enabled is True
    assert cfg.port == 9001
    assert cfg.data_dir == "./d"


# ------------------------------------------------------------- accounts / login flow
@pytest.fixture
def acc_cfg(tmp_path):
    return Config(data_dir=str(tmp_path), auth_enabled=True)


def test_default_admin_login_and_forced_password_change(acc_cfg):
    c = TestClient(create_app(acc_cfg))
    assert c.post("/api/login", json={"username": "admin", "password": "nope"}).status_code == 401
    r = c.post("/api/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    assert c.get("/api/whoami").json()["username"] == "admin"
    assert (
        c.post(
            "/api/password", json={"current_password": "admin", "new_password": "s3cret!"}
        ).status_code
        == 200
    )
    c2 = TestClient(create_app(acc_cfg))
    assert c2.post("/api/login", json={"username": "admin", "password": "admin"}).status_code == 401
    assert (
        c2.post("/api/login", json={"username": "admin", "password": "s3cret!"}).status_code == 200
    )


def test_admin_creates_namespace_and_user_who_cruds(acc_cfg):
    admin = TestClient(create_app(acc_cfg))
    admin.post("/api/login", json={"username": "admin", "password": "admin"})
    assert admin.post("/api/namespaces", json={"name": "projects"}).status_code == 201
    assert (
        admin.post(
            "/api/accounts",
            json={"username": "alice", "password": "pw1234", "grants": {"projects": "admin"}},
        ).status_code
        == 201
    )

    # alice logs in and works with tables/rows via her session cookie
    alice = TestClient(create_app(acc_cfg))
    alice.post("/api/login", json={"username": "alice", "password": "pw1234"})
    assert alice.post("/api/projects", json={"name": "people"}).status_code == 201
    row = alice.post("/api/projects/people/rows", json={"name": "x", "n": 1})
    assert row.status_code == 201
    assert set(alice.get("/api/projects/people/rows").json()["columns"]) == {"name", "n"}
    # cannot touch a namespace she wasn't granted
    assert alice.get("/api/secret").status_code == 403

    # alice mints her own API key and uses it (no cookie) with her permissions
    token = alice.post("/api/me/keys", json={"name": "ci"}).json()["token"]
    api = TestClient(create_app(acc_cfg))
    assert (
        api.post(
            "/api/projects/people/rows", json={"name": "y"}, headers={"X-API-Key": token}
        ).status_code
        == 201
    )
    assert api.get("/api/secret", headers={"X-API-Key": token}).status_code == 403


def test_non_admin_user_cannot_manage(acc_cfg):
    admin = TestClient(create_app(acc_cfg))
    admin.post("/api/login", json={"username": "admin", "password": "admin"})
    admin.post("/api/namespaces", json={"name": "projects"})
    admin.post(
        "/api/accounts",
        json={"username": "alice", "password": "pw1234", "grants": {"projects": "admin"}},
    )
    alice = TestClient(create_app(acc_cfg))
    alice.post("/api/login", json={"username": "alice", "password": "pw1234"})
    assert alice.post("/api/namespaces", json={"name": "x"}).status_code == 403
    assert alice.get("/api/accounts").status_code == 403


def test_session_secret_shared_across_instances(tmp_path):
    cfg = Config(data_dir=str(tmp_path), auth_enabled=True)
    a = TestClient(create_app(cfg))
    b = TestClient(create_app(cfg))  # separate "worker" on same data dir
    cookie = a.post("/api/login", json={"username": "admin", "password": "admin"}).cookies.get(
        "tds_session"
    )
    assert b.get("/api/whoami", cookies={"tds_session": cookie}).json()["authenticated"] is True
