

## Test Data Service

A working prototype of the above. Data is stored as JSON files on disk
(one file per namespace), exposed through a REST API and a web interface,
with optional role-based access control.

### Install & run

```bash
# from PyPI (once published)
pip install test-data-service

# start the server (API + web UI) on http://127.0.0.1:8000
tds serve                       # or: python -m testdataservice serve
# open http://127.0.0.1:8000 in a browser
```

From source, for development:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/tds serve
```

Packaging/release details (build, `twine`, GitHub Actions publish-on-release)
are in [CONTRIBUTING.md](CONTRIBUTING.md).

#### Configure with a YAML file

Drop a `tds.yml` (or `tds.yaml`) in your project root and just run `tds serve` —
no flags needed. Environment variables override the file.

```yaml
# tds.yml
data_dir: ./data        # where namespace files + .auth live
auth: true              # turn on accounts / access control
host: 127.0.0.1
port: 8000
```

### Data model

Three levels: **namespace → table → row**.

* A **namespace** is the unit of access control.
* A namespace holds any number of **tables**.
* A table holds **rows**, and each row is a record with an **arbitrary set of
  fields** — the "columns" are dynamic. Different rows may have different fields;
  a table's column set is just the union of the fields present across its rows.
  Field values can be any JSON (string, number, boolean, array, object, null).

### REST API

| Method   | Path                              | Permission | Description                     |
|----------|-----------------------------------|------------|---------------------------------|
| `GET`    | `/api/health`                     | —          | Health + auth status            |
| `GET`    | `/api/whoami`                     | —          | Caller's access summary         |
| `GET`    | `/api/namespaces`                 | any key    | List accessible namespaces      |
| `POST`   | `/api/namespaces`                 | admin      | Create a namespace              |
| `DELETE` | `/api/{ns}`                       | write `ns` | Delete a namespace              |
| `GET`    | `/api/{ns}`                       | read `ns`  | List tables                     |
| `POST`   | `/api/{ns}`                       | write `ns` | Create a table (`{"name": …}`)  |
| `DELETE` | `/api/{ns}/{table}`               | write `ns` | Delete a table                  |
| `GET`    | `/api/{ns}/{table}/rows`          | read `ns`  | List rows + current columns     |
| `POST`   | `/api/{ns}/{table}/rows`          | write `ns` | Create a row (body = its fields)|
| `GET`    | `/api/{ns}/{table}/rows/{id}`     | read `ns`  | Get one row                     |
| `PUT`    | `/api/{ns}/{table}/rows/{id}`     | write `ns` | Replace a row's fields          |
| `DELETE` | `/api/{ns}/{table}/rows/{id}`     | write `ns` | Delete a row                    |

```bash
curl -X POST localhost:8000/api/namespaces -H 'Content-Type: application/json' -d '{"name":"demo"}'
curl -X POST localhost:8000/api/demo       -H 'Content-Type: application/json' -d '{"name":"users"}'

# a row IS its fields — any shape you like
curl -X POST localhost:8000/api/demo/users/rows \
  -H 'Content-Type: application/json' \
  -d '{"name":"alice","age":30,"active":true}'
# -> {"id":"<row-id>","updated_at":"...","fields":{...}}

curl localhost:8000/api/demo/users/rows
# -> {"columns":["name","age","active", ...], "rows":[{"id":..., "fields":{...}}, ...]}
```

(The names `namespaces`, `health`, `whoami`, `keys`, `accounts`, `login`,
`logout`, `password`, and `me` are reserved and can't be used as namespaces.)

The API is self-documenting via OpenAPI:

- **`/docs`** — Swagger UI (also linked as **API docs** in the web interface header)
- **`/redoc`** — ReDoc
- **`/openapi.json`** — raw OpenAPI schema

When auth is enabled, each operation in Swagger exposes an `X-API-Key` header
field you can fill in to try requests.

### CSV import / export (web UI only, per table)

Each table in the web UI has **Download CSV** / **Import CSV** buttons. A table
maps naturally to CSV: the header row is the column names, each line is a row.
These are built on the rows API (no dedicated CSV endpoints). On import, each
cell is parsed leniently — `30` → number, `true` → boolean, `[1,2]` → array,
anything else → string.

```csv
name,age,active
alice,30,true
bob,25,false
```

### Access control (accounts + API keys)

Off by default. **Turn it on** with `auth: true` in `tds.yml`, `--auth`, or
`TDS_AUTH_ENABLED=1`. There are two credential types, and you can use both:

* **User accounts** — username + password. Humans log into the **web UI**
  (session cookie). Passwords are hashed with scrypt (a slow, salted KDF); the
  plaintext is never stored.
* **API keys** — long random tokens for scripts/CI. Only a SHA-256 hash is
  stored; the token is shown **once**, at creation.

Access is **scoped to each namespace**. A role of `admin` means full CRUD on
that namespace; `reader` means read-only. A **global admin** manages everything.

#### First run

On first start with auth on, a default admin account is created:
**username `admin`, password `admin`** — and the web UI **requires you to set a
new password** before anything else.

#### What the admin does (from the web UI)

1. **Create namespaces** (the admin-only form in the sidebar).
2. **Create user accounts** scoped to namespaces — e.g. `alice` with
   `projects=admin` (full CRUD on `projects`). Users then log in and
   create/read/update/delete data in their namespaces, from the web UI **and**
   the API.

#### Using the API as a user

Each signed-in user can mint a personal **API key** (web UI → *My API keys*, or
`POST /api/me/keys`) that inherits their namespace permissions:

```bash
# the token is returned once; then use it as a header
curl -X POST localhost:8000/api/projects/people/rows \
  -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"alice","role":"qa"}'
```

#### Auth & admin endpoints (summary)

| Method   | Path                    | Who    | Purpose                              |
|----------|-------------------------|--------|--------------------------------------|
| `POST`   | `/api/login`            | anyone | Log in (sets session cookie)         |
| `POST`   | `/api/logout`           | anyone | Clear session                        |
| `POST`   | `/api/password`         | user   | Change your own password             |
| `POST`   | `/api/namespaces`       | admin  | Create a namespace                   |
| `GET`/`POST`/`DELETE` | `/api/accounts[/{user}]` | admin | Manage user accounts     |
| `GET`/`POST`/`DELETE` | `/api/me/keys[/{id}]`    | user  | Manage your own API keys |
| `GET`/`POST`/`PUT`/`DELETE` | `/api/keys[/{id}]` | admin | Manage standalone (machine) keys |

Standalone machine-to-machine keys (not tied to an account) are still managed by
an admin at `/api/keys`. For bootstrapping you may seed keys via `TDS_API_KEYS`
or an admin token via `TDS_ADMIN_KEY` (both hashed in memory, never written to
disk).

### Configuration

Settings come from `tds.yml`/`tds.yaml` in the project root, overridden by
environment variables:

| YAML key   | Env var            | Default      | Purpose                                  |
|------------|--------------------|--------------|------------------------------------------|
| `data_dir` | `TDS_DATA_DIR`     | `./tds-data` | Data + `.auth/` live here                |
| `storage`  | `TDS_STORAGE`      | `sqlite`     | Storage backend: `sqlite` or `file`      |
| `db_path`  | `TDS_DB_PATH`      | `<data_dir>/tds.db` | SQLite DB path (sqlite backend)   |
| `auth`     | `TDS_AUTH_ENABLED` | `false`      | Turn access control on/off               |
| `host`     | —                  | `127.0.0.1`  | Bind host                                |
| `port`     | —                  | `8000`       | Bind port                                |
| `secret`   | `TDS_SECRET`       | *(generated)*| Signs web-UI session cookies             |
| `admin_key`| `TDS_ADMIN_KEY`    | *(empty)*    | Bootstrap machine admin token            |
| `api_keys` | `TDS_API_KEYS`     | *(empty)*    | Seed machine keys + grants               |
| —          | `TDS_KEY_PEPPER`   | *(empty)*    | Optional pepper for API-key hashing      |

#### Storage backend

Data is kept in **SQLite** by default — a single `tds.db` file the app creates
in `data_dir` on first start (no setup, no server). Rows store their dynamic
fields in a JSON column, so the schema is fixed while the data stays free-form.
Set `storage: file` to use one JSON file per namespace instead (human-readable
and dependency-free, but it rewrites a whole file per change and isn't safe
across processes). Either way, point `data_dir` at a persistent volume.

Credentials always live under `<data_dir>/.auth/` (`accounts.json`,
`keys.json`, `secret`) — hashed/secret material, never plaintext.

### Tests

```bash
.venv/bin/python -m pytest
```
