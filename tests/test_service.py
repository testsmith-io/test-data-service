import pytest
from fastapi.testclient import TestClient

from testdataservice import Storage, ValueType
from testdataservice.app import create_app
from testdataservice.config import Config
from testdataservice.storage import KeyExistsError, KeyNotFoundError


# ------------------------------------------------------------- storage
def test_storage_roundtrip_all_types(tmp_path):
    s = Storage(tmp_path)
    s.set("ns", "name", "alice")
    s.set("ns", "active", True)
    s.set("ns", "count", 42)
    s.set("ns", "tags", ["a", "b"])

    assert s.get("ns", "name").type == ValueType.STRING
    assert s.get("ns", "active").type == ValueType.BOOLEAN
    assert s.get("ns", "count").type == ValueType.NUMBER
    assert s.get("ns", "tags").value == ["a", "b"]
    assert {e.key for e in s.list_entries("ns")} == {"name", "active", "count", "tags"}


def test_storage_persists_to_disk(tmp_path):
    Storage(tmp_path).set("ns", "k", "v")
    assert Storage(tmp_path).get("ns", "k").value == "v"


def test_storage_create_rejects_duplicate(tmp_path):
    s = Storage(tmp_path)
    s.create("ns", "k", "v")
    with pytest.raises(KeyExistsError):
        s.create("ns", "k", "v2")


def test_storage_update_requires_existing(tmp_path):
    s = Storage(tmp_path)
    with pytest.raises(KeyNotFoundError):
        s.update("ns", "k", "v")
    s.create("ns", "k", "v")
    assert s.update("ns", "k", "v2").value == "v2"


def test_storage_delete_and_missing(tmp_path):
    s = Storage(tmp_path)
    s.set("ns", "k", "v")
    s.delete("ns", "k")
    with pytest.raises(KeyNotFoundError):
        s.get("ns", "k")


def test_storage_type_validation(tmp_path):
    s = Storage(tmp_path)
    with pytest.raises(ValueError):
        s.set("ns", "k", "not-a-number", value_type="number")


def test_storage_rejects_reserved_namespace(tmp_path):
    s = Storage(tmp_path)
    with pytest.raises(ValueError):
        s.set("namespaces", "k", "v")


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


def test_api_create_then_read_list_delete(client):
    # POST to the namespace (collection) with the key in the body -> 201
    assert (
        client.post("/api/t", json={"key": "k", "value": [1, 2], "type": "array"}).status_code
        == 201
    )
    assert client.get("/api/t/k").json()["value"] == [1, 2]
    assert client.get("/api/t").json()["entries"][0]["key"] == "k"
    assert client.delete("/api/t/k").status_code == 200
    assert client.get("/api/t/k").status_code == 404


def test_api_post_conflict_on_existing_key(client):
    assert client.post("/api/t", json={"key": "k", "value": "a"}).status_code == 201
    assert client.post("/api/t", json={"key": "k", "value": "b"}).status_code == 409
    # value is unchanged by the rejected create
    assert client.get("/api/t/k").json()["value"] == "a"


def test_api_post_requires_key_in_body(client):
    # Missing "key" is a validation error.
    assert client.post("/api/t", json={"value": "a"}).status_code == 422


def test_api_post_rejects_blank_key(client):
    assert client.post("/api/t", json={"key": "", "value": "a"}).status_code == 422
    assert client.post("/api/t", json={"key": "   ", "value": "a"}).status_code == 422


def test_api_post_sets_location_header(client):
    r = client.post("/api/t", json={"key": "greeting", "value": "hi"})
    assert r.status_code == 201
    assert r.headers["location"] == "/api/t/greeting"


def test_api_delete_missing_namespace_is_404(client):
    # Consistent with deleting a missing entry (also 404).
    assert client.delete("/api/ghost").status_code == 404


def test_api_delete_namespace_ok(client):
    client.post("/api/t", json={"key": "k", "value": "v"})
    assert client.delete("/api/t").status_code == 200
    assert client.delete("/api/t").status_code == 404  # now gone


def test_api_put_updates_existing_key(client):
    client.post("/api/t", json={"key": "k", "value": "a"})
    assert client.put("/api/t/k", json={"value": "b"}).status_code == 200
    assert client.get("/api/t/k").json()["value"] == "b"


def test_api_put_missing_key_is_404(client):
    assert client.put("/api/t/missing", json={"value": "b"}).status_code == 404


def test_api_rejects_bad_type(client):
    assert (
        client.post("/api/t", json={"key": "k", "value": "x", "type": "number"}).status_code == 422
    )


def test_api_reserved_namespace_is_400(client):
    # Reserved names collide with literal routes and are rejected on creation.
    assert client.post("/api/namespaces", json={"name": "health"}).status_code == 400


def test_web_ui_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Test Data Service" in r.text


# ------------------------------------------------------------- API (auth on)
@pytest.fixture
def auth_client(tmp_path):
    cfg = Config(
        data_dir=str(tmp_path),
        auth_enabled=True,
        api_keys={
            "root-key": {"*": "admin"},  # admin everywhere
            "global-read": {"*": "reader"},  # reader everywhere
            "team-key": {"alpha": "admin", "beta": "reader"},  # scoped
        },
    )
    return TestClient(create_app(cfg))


def test_auth_requires_key(auth_client):
    assert auth_client.get("/api/namespaces").status_code == 401


def test_auth_global_reader_cannot_write(auth_client):
    h = {"X-API-Key": "global-read"}
    assert auth_client.get("/api/namespaces", headers=h).status_code == 200
    assert auth_client.post("/api/t", json={"key": "k", "value": "v"}, headers=h).status_code == 403


def test_auth_root_can_write_any_namespace(auth_client):
    h = {"X-API-Key": "root-key"}
    assert (
        auth_client.post("/api/anything", json={"key": "k", "value": "v"}, headers=h).status_code
        == 201
    )


def test_auth_namespace_scoped_admin(auth_client):
    h = {"X-API-Key": "team-key"}
    # admin on alpha -> can create
    assert (
        auth_client.post("/api/alpha", json={"key": "k", "value": "v"}, headers=h).status_code
        == 201
    )
    # reader on beta -> can read, cannot write
    assert auth_client.get("/api/beta", headers=h).status_code == 200
    assert (
        auth_client.post("/api/beta", json={"key": "k", "value": "v"}, headers=h).status_code == 403
    )
    # no grant on gamma -> forbidden for both read and write
    assert auth_client.get("/api/gamma", headers=h).status_code == 403
    assert (
        auth_client.post("/api/gamma", json={"key": "k", "value": "v"}, headers=h).status_code
        == 403
    )


def test_auth_namespace_list_is_filtered(auth_client):
    root = {"X-API-Key": "root-key"}
    auth_client.post("/api/alpha", json={"key": "k", "value": "v"}, headers=root)
    auth_client.post("/api/secret", json={"key": "k", "value": "v"}, headers=root)
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
    r2 = auth_client.get("/api/whoami", headers={"X-API-Key": "root-key"})
    assert r2.json()["is_admin"] is True


# ------------------------------------------------------------- keystore (hashing)
def test_keystore_hashes_token_not_plaintext(tmp_path):
    from testdataservice.keystore import KeyStore, hash_token

    ks = KeyStore(tmp_path / "keys.json")
    token, record = ks.create("ci", {"projects": "admin"})
    # The on-disk file must not contain the plaintext token, only its hash.
    raw = (tmp_path / "keys.json").read_text()
    assert token not in raw
    assert hash_token(token) in raw
    # Resolve works by hashing the presented token.
    assert ks.resolve_by_hash(hash_token(token)) == {"projects": "admin"}
    assert ks.resolve_by_hash(hash_token("wrong")) is None


def test_keystore_update_and_delete(tmp_path):
    from testdataservice.keystore import KeyStore

    ks = KeyStore(tmp_path / "keys.json")
    _, record = ks.create("k", {"a": "reader"})
    assert ks.update_grants(record.id, {"a": "admin"}).grants == {"a": "admin"}
    assert ks.delete(record.id) is True
    assert ks.delete(record.id) is False


# ------------------------------------------------------------- admin key management API
def test_admin_can_mint_key_and_it_works(auth_client):
    admin = {"X-API-Key": "root-key"}
    # Mint a key scoped to namespace "alpha" as reader.
    r = auth_client.post(
        "/api/keys",
        json={"name": "qa", "grants": {"alpha": "reader"}},
        headers=admin,
    )
    assert r.status_code == 201
    token = r.json()["token"]
    assert token  # plaintext returned exactly once

    # The new key can read alpha but not write it, and cannot see beta.
    h = {"X-API-Key": token}
    auth_client.post("/api/alpha", json={"key": "k", "value": "v"}, headers=admin)
    assert auth_client.get("/api/alpha", headers=h).status_code == 200
    assert (
        auth_client.post("/api/alpha", json={"key": "x", "value": "v"}, headers=h).status_code
        == 403
    )
    assert auth_client.get("/api/beta", headers=h).status_code == 403


def test_non_admin_cannot_manage_keys(auth_client):
    # team-key is admin only on "alpha", not a *global* admin.
    h = {"X-API-Key": "team-key"}
    assert auth_client.get("/api/keys", headers=h).status_code == 403
    assert (
        auth_client.post(
            "/api/keys", json={"name": "x", "grants": {"alpha": "reader"}}, headers=h
        ).status_code
        == 403
    )


def test_admin_list_and_revoke_key(auth_client):
    admin = {"X-API-Key": "root-key"}
    created = auth_client.post(
        "/api/keys", json={"name": "temp", "grants": {"*": "reader"}}, headers=admin
    ).json()
    key_id, token = created["id"], created["token"]

    listed = auth_client.get("/api/keys", headers=admin).json()["keys"]
    assert any(k["id"] == key_id for k in listed)
    assert all("token" not in k and "token_hash" not in k for k in listed)

    assert auth_client.delete(f"/api/keys/{key_id}", headers=admin).status_code == 200
    # Revoked key no longer authenticates.
    assert auth_client.get("/api/namespaces", headers={"X-API-Key": token}).status_code == 401


def test_create_key_rejects_bad_role(auth_client):
    admin = {"X-API-Key": "root-key"}
    r = auth_client.post(
        "/api/keys", json={"name": "x", "grants": {"alpha": "superuser"}}, headers=admin
    )
    assert r.status_code == 422


def test_minted_key_persists_across_restart(tmp_path):
    # A key minted via the admin API is stored (hashed) on disk, so it survives
    # a fresh app instance pointed at the same data dir.
    cfg = Config(data_dir=str(tmp_path), auth_enabled=True, admin_key="boot-admin")
    c1 = TestClient(create_app(cfg))
    token = c1.post(
        "/api/keys",
        json={"name": "p", "grants": {"*": "reader"}},
        headers={"X-API-Key": "boot-admin"},
    ).json()["token"]

    c2 = TestClient(create_app(cfg))  # new instance, same disk
    assert c2.get("/api/namespaces", headers={"X-API-Key": token}).status_code == 200


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


def test_env_overrides_yaml(tmp_path, monkeypatch):
    (tmp_path / "tds.yml").write_text("auth: false\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TDS_AUTH_ENABLED", "1")
    assert Config.load().auth_enabled is True


# ------------------------------------------------------------- accounts / login flow
@pytest.fixture
def acc_cfg(tmp_path):
    # Auth on; the default admin/admin account is seeded automatically.
    return Config(data_dir=str(tmp_path), auth_enabled=True)


def test_default_admin_login_and_forced_password_change(acc_cfg):
    c = TestClient(create_app(acc_cfg))
    assert c.post("/api/login", json={"username": "admin", "password": "nope"}).status_code == 401
    r = c.post("/api/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    # Session cookie now identifies the admin.
    who = c.get("/api/whoami").json()
    assert who["username"] == "admin" and who["is_admin"] and who["must_change_password"]
    # Change the password.
    assert (
        c.post(
            "/api/password", json={"current_password": "admin", "new_password": "s3cret!"}
        ).status_code
        == 200
    )
    # Old password no longer works; new one does (fresh client).
    c2 = TestClient(create_app(acc_cfg))
    assert c2.post("/api/login", json={"username": "admin", "password": "admin"}).status_code == 401
    assert (
        c2.post("/api/login", json={"username": "admin", "password": "s3cret!"}).status_code == 200
    )


def test_admin_creates_namespace_and_user_who_cruds(acc_cfg):
    admin = TestClient(create_app(acc_cfg))
    admin.post("/api/login", json={"username": "admin", "password": "admin"})
    assert admin.post("/api/namespaces", json={"name": "projects"}).status_code == 201
    # Create a user scoped to "projects" with full CRUD (admin role on that ns).
    assert (
        admin.post(
            "/api/accounts",
            json={"username": "alice", "password": "pw1234", "grants": {"projects": "admin"}},
        ).status_code
        == 201
    )
    # Can't grant a namespace that doesn't exist.
    assert (
        admin.post(
            "/api/accounts",
            json={"username": "bob", "password": "pw1234", "grants": {"ghost": "admin"}},
        ).status_code
        == 422
    )

    # Alice logs in and CRUDs via her session cookie.
    alice = TestClient(create_app(acc_cfg))
    alice.post("/api/login", json={"username": "alice", "password": "pw1234"})
    assert alice.post("/api/projects", json={"key": "k", "value": "v"}).status_code == 201
    assert alice.get("/api/projects/k").json()["value"] == "v"
    assert alice.put("/api/projects/k", json={"value": "v2"}).status_code == 200
    assert alice.delete("/api/projects/k").status_code == 200
    # Alice cannot touch a namespace she wasn't granted.
    assert alice.get("/api/secret").status_code == 403

    # Alice mints her own API key and uses it (no cookie) with her permissions.
    token = alice.post("/api/me/keys", json={"name": "ci"}).json()["token"]
    api = TestClient(create_app(acc_cfg))
    assert (
        api.post(
            "/api/projects", json={"key": "k2", "value": 1}, headers={"X-API-Key": token}
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
