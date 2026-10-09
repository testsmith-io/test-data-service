

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

Each entry is a `key` / `value` pair inside a **namespace**. A value is one
of four types: `string`, `boolean`, `number`, or `array`. The type is stored
alongside the value and validated on write.

### REST API

`POST` targets the namespace (collection) with the key **in the body**; `PUT`
targets a specific entry URI. They are not interchangeable — `POST` only creates,
`PUT` only updates.

| Method   | Path                    | Permission | Description                          |
|----------|-------------------------|------------|--------------------------------------|
| `GET`    | `/api/health`           | —          | Health + auth status                 |
| `GET`    | `/api/whoami`           | —          | Caller's access summary              |
| `GET`    | `/api/namespaces`       | any key    | List accessible namespaces           |
| `GET`    | `/api/{ns}`             | read `ns`  | List entries in a namespace          |
| `POST`   | `/api/{ns}`             | write `ns` | Create an entry (`409` if key exists) |
| `GET`    | `/api/{ns}/{key}`       | read `ns`  | Get one entry                        |
| `PUT`    | `/api/{ns}/{key}`       | write `ns` | Update an entry (`404` if missing)   |
| `DELETE` | `/api/{ns}/{key}`       | write `ns` | Delete an entry                      |
| `DELETE` | `/api/{ns}`             | write `ns` | Delete a namespace                   |

```bash
# create — POST to the namespace, key in the body
curl -X POST localhost:8000/api/demo \
  -H 'Content-Type: application/json' \
  -d '{"key":"greeting","value":"hello","type":"string"}'

# update — PUT to the entry's URI
curl -X PUT localhost:8000/api/demo/greeting \
  -H 'Content-Type: application/json' \
  -d '{"value":"hi there","type":"string"}'

curl localhost:8000/api/demo          # list entries
```

(The names `namespaces`, `health`, and `whoami` are reserved and can't be used
as namespaces, since they'd collide with the routes above.)

The API is self-documenting via OpenAPI. Interactive docs are available at:

- **`/docs`** — Swagger UI (also linked as **API docs** in the web interface header)
- **`/redoc`** — ReDoc
- **`/openapi.json`** — raw OpenAPI schema

When auth is enabled, each operation in Swagger exposes an `X-API-Key` header
field you can fill in to try requests.

### CSV import / export (web UI only)

The web interface has **Download CSV** / **Import CSV** buttons on each
namespace. These are a convenience of the web UI only — they are built on the
regular entry API (export reads the entries; import creates/updates each row),
so there are no dedicated CSV endpoints. The CSV has three columns —
`key`, `type`, `value`:

```csv
key,type,value
name,string,alice
count,number,7
tags,array,"[""x"", ""y""]"
```

On import the `type` column is optional — if omitted, each value's type is
inferred from its text.

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
curl -X POST localhost:8000/api/projects \
  -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"key":"env","value":"staging","type":"string"}'
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
| `data_dir` | `TDS_DATA_DIR`     | `./tds-data` | Namespace files + `.auth/` live here     |
| `auth`     | `TDS_AUTH_ENABLED` | `false`      | Turn access control on/off               |
| `host`     | —                  | `127.0.0.1`  | Bind host                                |
| `port`     | —                  | `8000`       | Bind port                                |
| `secret`   | `TDS_SECRET`       | *(generated)*| Signs web-UI session cookies             |
| `admin_key`| `TDS_ADMIN_KEY`    | *(empty)*    | Bootstrap machine admin token            |
| `api_keys` | `TDS_API_KEYS`     | *(empty)*    | Seed machine keys + grants               |
| —          | `TDS_KEY_PEPPER`   | *(empty)*    | Optional pepper for API-key hashing      |

Credentials live under `<data_dir>/.auth/` (`accounts.json`, `keys.json`,
`secret`) — all hashed/secret material, never plaintext passwords or tokens.

### Tests

```bash
.venv/bin/python -m pytest
```
