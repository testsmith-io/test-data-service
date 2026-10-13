"use strict";

let currentNs = null;
let currentTable = null;
let editingRowId = null;
let currentColumns = [];
let rowsById = {};
let me = { authenticated: false, is_admin: false, username: null, grants: {} };
let knownNamespaces = [];

const $ = (id) => document.getElementById(id);
const authOn = () => document.body.dataset.auth === "on";
const enc = encodeURIComponent;
const canWrite = (ns) =>
  !!me.is_admin || me.grants?.[ns] === "admin" || me.grants?.["*"] === "admin";

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
// Lenient value coercion: numbers/booleans/arrays/objects via JSON, else string.
function coerce(raw) {
  const t = raw.trim();
  if (t === "") return "";
  try { return JSON.parse(t); } catch (_) { return raw; }
}

async function api(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

function setStatus(id, msg, kind) {
  const el = $(id);
  if (!el) return;
  el.textContent = msg || "";
  el.className = "status" + (kind ? " " + kind : "");
}

// -- value helpers -----------------------------------------------------
const renderValue = (v) => (typeof v === "string" ? v : JSON.stringify(v));
const renderGrants = (g) =>
  Object.entries(g).map(([ns, role]) => (ns === "*" ? `${role} (all)` : `${role}:${ns}`)).join(", ");

// ======================================================================
//  Session / views
// ======================================================================
async function refresh() {
  me = await api("GET", "/api/whoami");
  if (authOn() && !me.authenticated) return showAuth("login");
  if (me.must_change_password) return showAuth("pw");

  // Logged in: show the app shell with the right tabs.
  showAuth(null);
  $("appShell").hidden = false;
  $("nav").hidden = false;

  const admin = !!me.is_admin;
  $("newNamespace").hidden = !admin;          // create-namespace control
  $("navAccounts").hidden = !admin;
  $("navKeys").hidden = !admin;
  $("navMyKeys").hidden = !me.username;       // real accounts only

  const info = $("sessionInfo");
  if (authOn()) {
    info.hidden = false;
    $("whoUser").textContent = me.username || "";
    $("whoAccess").textContent = me.access ? `· ${me.access}` : "";
  } else {
    info.hidden = true;
  }

  showTab("data");
  await refreshNamespaces();
  if (admin) { loadAccounts(); loadKeys(); }
  if (me.username) loadMyKeys();
}

// Toggle the login / password-change full-screen cards. Pass null for neither.
function showAuth(which) {
  $("loginView").hidden = which !== "login";
  $("pwView").hidden = which !== "pw";
  if (which) {
    $("appShell").hidden = true;
    $("nav").hidden = true;
    $("sessionInfo").hidden = true;
  }
}

// Switch which app tab/view is visible.
function showTab(name) {
  for (const v of document.querySelectorAll("#appShell .view")) {
    v.hidden = v.id !== `view-${name}`;
  }
  for (const b of document.querySelectorAll("#nav .tab")) {
    b.classList.toggle("active", b.dataset.tab === name);
  }
}

document.getElementById("nav").addEventListener("click", (ev) => {
  const b = ev.target.closest(".tab");
  if (b) showTab(b.dataset.tab);
});

// -- login / logout / password ----------------------------------------
$("loginForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    await api("POST", "/api/login", {
      username: $("loginUser").value.trim(),
      password: $("loginPass").value,
    });
    $("loginPass").value = "";
    setStatus("loginStatus", "");
    await refresh();
  } catch (err) {
    setStatus("loginStatus", err.message, "error");
  }
});

$("logoutBtn").addEventListener("click", async () => {
  try { await api("POST", "/api/logout"); } catch (_) {}
  currentNs = null;
  await refresh();
});

$("pwForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    await api("POST", "/api/password", {
      current_password: $("pwCurrent").value,
      new_password: $("pwNew").value,
    });
    $("pwCurrent").value = $("pwNew").value = "";
    await refresh();
  } catch (err) {
    setStatus("pwStatus", err.message, "error");
  }
});

// ======================================================================
//  Namespaces + entries
// ======================================================================
async function refreshNamespaces() {
  const ul = $("namespaces");
  try {
    const { namespaces } = await api("GET", "/api/namespaces");
    knownNamespaces = namespaces.slice();
    populateNsSelect();
    const names = new Set(namespaces);
    if (currentNs) names.add(currentNs);
    const sorted = [...names].sort();
    ul.innerHTML = "";
    if (!sorted.length) { ul.innerHTML = '<li class="empty">none yet</li>'; return; }
    for (const ns of sorted) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.className = "ns" + (ns === currentNs ? " active" : "");
      btn.dataset.ns = ns;
      btn.textContent = ns;
      li.appendChild(btn);
      ul.appendChild(li);
    }
  } catch (err) {
    setStatus("status", err.message, "error");
  }
}

async function openNamespace(ns) {
  currentNs = ns;
  currentTable = null;
  $("currentNs").textContent = ns;
  document.querySelectorAll(".ns").forEach((b) => b.classList.toggle("active", b.dataset.ns === ns));
  $("tablesBar").hidden = false;
  $("newTableBtn").hidden = !canWrite(ns);
  $("tableView").hidden = true;
  await loadTables();
}

async function loadTables() {
  const wrap = $("tablePills");
  wrap.innerHTML = "";
  try {
    const { tables } = await api("GET", `/api/${enc(currentNs)}`);
    if (!tables.length) wrap.innerHTML = '<span class="empty">no tables yet</span>';
    for (const t of tables) {
      const b = document.createElement("button");
      b.className = "pill-btn" + (t === currentTable ? " active" : "");
      b.dataset.table = t;
      b.textContent = t;
      wrap.appendChild(b);
    }
  } catch (err) {
    setStatus("status", err.message, "error");
  }
}

async function openTable(t) {
  currentTable = t;
  editingRowId = null;
  const w = canWrite(currentNs);
  $("currentTable").textContent = t;
  $("tableView").hidden = false;
  $("rowForm").hidden = true;
  $("addRowBtn").hidden = !w;
  $("deleteTableBtn").hidden = !w;
  $("importCsv").closest(".import-label").hidden = !w;
  document.querySelectorAll("#tablePills .pill-btn").forEach(
    (b) => b.classList.toggle("active", b.dataset.table === t));
  await loadRows();
}

async function loadRows() {
  const table = $("rows");
  const thead = $("rowsHead");
  const tbody = table.querySelector("tbody");
  rowsById = {};
  try {
    const { columns, rows } = await api("GET", `/api/${enc(currentNs)}/${enc(currentTable)}/rows`);
    currentColumns = columns;
    const w = canWrite(currentNs);
    $("emptyMsg").hidden = rows.length > 0;
    table.hidden = rows.length === 0;
    thead.innerHTML =
      columns.map((c) => `<th>${escapeHtml(c)}</th>`).join("") + (w ? "<th></th>" : "");
    tbody.innerHTML = "";
    for (const r of rows) {
      rowsById[r.id] = r.fields;
      let cells = columns
        .map((c) => `<td>${r.fields[c] === undefined ? "" : escapeHtml(renderValue(r.fields[c]))}</td>`)
        .join("");
      if (w) {
        cells +=
          `<td class="rowacts"><button data-editrow="${r.id}">edit</button>` +
          ` <button class="link-danger" data-delrow="${r.id}">delete</button></td>`;
      }
      const tr = document.createElement("tr");
      tr.innerHTML = cells;
      tbody.appendChild(tr);
    }
    setStatus("status", "");
  } catch (err) {
    setStatus("status", err.message, "error");
  }
}

// -- dynamic add/edit row form ----------------------------------------
function renderRowForm(fields) {
  const cols = [...new Set([...currentColumns, ...Object.keys(fields)])];
  const box = $("rowFields");
  box.innerHTML = "";
  if (!cols.length) {
    box.innerHTML = '<p class="muted">Add a column below to start.</p>';
  }
  for (const c of cols) addFieldInput(c, fields[c]);
}

function addFieldInput(name, value) {
  if ($("rowFields").querySelector(`[data-field="${CSS.escape(name)}"]`)) return;
  const label = document.createElement("label");
  label.className = "field";
  const shown = value === undefined ? "" : typeof value === "string" ? value : JSON.stringify(value);
  label.innerHTML = `<span>${escapeHtml(name)}</span>`;
  const input = document.createElement("input");
  input.dataset.field = name;
  input.value = shown;
  label.appendChild(input);
  $("rowFields").appendChild(label);
}

function openRowForm(rowId) {
  editingRowId = rowId;
  $("rowFormTitle").textContent = rowId ? "Edit row" : "Add row";
  renderRowForm(rowId ? rowsById[rowId] || {} : {});
  $("rowForm").hidden = false;
}

$("addRowBtn").addEventListener("click", () => openRowForm(null));
$("cancelRowBtn").addEventListener("click", () => { $("rowForm").hidden = true; });

$("addFieldBtn").addEventListener("click", () => {
  const name = $("newFieldName").value.trim();
  if (!name) return;
  addFieldInput(name, "");
  $("newFieldName").value = "";
});

$("rowForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const fields = {};
  for (const input of $("rowFields").querySelectorAll("input[data-field]")) {
    if (input.value.trim() !== "") fields[input.dataset.field] = coerce(input.value);
  }
  const base = `/api/${enc(currentNs)}/${enc(currentTable)}/rows`;
  try {
    if (editingRowId) await api("PUT", `${base}/${enc(editingRowId)}`, fields);
    else await api("POST", base, fields);
    $("rowForm").hidden = true;
    setStatus("status", "Row saved.", "ok");
    await loadRows();
  } catch (err) {
    setStatus("status", err.message, "error");
  }
});

$("newNamespace").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const ns = $("nsInput").value.trim();
  if (!ns) return;
  try {
    await api("POST", "/api/namespaces", { name: ns });
    $("nsInput").value = "";
    await refreshNamespaces();
    await openNamespace(ns);
  } catch (err) {
    setStatus("status", err.message, "error");
  }
});

$("newTableBtn").addEventListener("click", async () => {
  const name = prompt("New table name:");
  if (!name || !name.trim()) return;
  try {
    await api("POST", `/api/${enc(currentNs)}`, { name: name.trim() });
    await loadTables();
    await openTable(name.trim());
  } catch (err) {
    setStatus("status", err.message, "error");
  }
});

$("deleteTableBtn").addEventListener("click", async () => {
  if (!currentTable) return;
  if (!confirm(`Delete table "${currentTable}" and all its rows?`)) return;
  try {
    await api("DELETE", `/api/${enc(currentNs)}/${enc(currentTable)}`);
    currentTable = null;
    $("tableView").hidden = true;
    await loadTables();
  } catch (err) {
    setStatus("status", err.message, "error");
  }
});

// ======================================================================
//  CSV per table (web-UI only; built on the rows API)
// ======================================================================
function csvEscape(s) {
  return /[",\n]/.test(s) ? '"' + String(s).replace(/"/g, '""') + '"' : s;
}
function parseCsvText(text) {
  const rows = []; let row = [], field = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else q = false; }
      else field += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n") { row.push(field); rows.push(row); row = []; field = ""; }
    else if (c !== "\r") field += c;
  }
  if (field.length || row.length) { row.push(field); rows.push(row); }
  return rows;
}
async function downloadCsv() {
  if (!currentNs || !currentTable) return;
  try {
    const { columns, rows } = await api("GET", `/api/${enc(currentNs)}/${enc(currentTable)}/rows`);
    const cell = (v) => (v === undefined ? "" : typeof v === "string" ? v : JSON.stringify(v));
    const lines = [columns.map(csvEscape).join(",")];
    for (const r of rows) lines.push(columns.map((c) => csvEscape(cell(r.fields[c]))).join(","));
    const blob = new Blob([lines.join("\n") + "\n"], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `${currentNs}-${currentTable}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    setStatus("status", `Downloaded "${currentNs}-${currentTable}.csv".`, "ok");
  } catch (err) { setStatus("status", err.message, "error"); }
}
async function importCsv(file) {
  if (!currentNs || !currentTable || !file) return;
  try {
    const grid = parseCsvText(await file.text()).filter((r) => r.some((c) => c.trim() !== ""));
    if (!grid.length) return;
    const header = grid[0].map((h) => h.trim());
    let n = 0;
    for (let i = 1; i < grid.length; i++) {
      const fields = {};
      header.forEach((col, j) => {
        const v = grid[i][j];
        if (col && v !== undefined && v.trim() !== "") fields[col] = coerce(v);
      });
      await api("POST", `/api/${enc(currentNs)}/${enc(currentTable)}/rows`, fields);
      n++;
    }
    setStatus("status", `Imported ${n} row${n === 1 ? "" : "s"}.`, "ok");
    await loadRows();
  } catch (err) { setStatus("status", `Import failed: ${err.message}`, "error"); }
}
$("downloadCsv").addEventListener("click", downloadCsv);
$("importCsv").addEventListener("change", (ev) => {
  importCsv(ev.target.files[0]);
  ev.target.value = "";
});

// ======================================================================
//  My API keys
// ======================================================================
async function loadMyKeys() {
  const tbody = $("myKeysTable").querySelector("tbody");
  tbody.innerHTML = "";
  try {
    const { keys } = await api("GET", "/api/me/keys");
    for (const k of keys) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td>${k.name}</td><td>${renderGrants(k.grants)}</td>` +
        `<td><code>${k.prefix}…</code></td>` +
        `<td><button class="link-danger" data-mykey="${k.id}">revoke</button></td>`;
      tbody.appendChild(tr);
    }
  } catch (err) { setStatus("myKeysStatus", err.message, "error"); }
}
$("myKeyForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const created = await api("POST", "/api/me/keys", { name: $("myKeyName").value.trim() });
    const tok = $("myNewToken");
    tok.hidden = false;
    tok.textContent = `New token (copy now — shown once): ${created.token}`;
    $("myKeyName").value = "";
    setStatus("myKeysStatus", "");
    loadMyKeys();
  } catch (err) { setStatus("myKeysStatus", err.message, "error"); }
});

// ======================================================================
//  Admin: accounts + standalone keys
// ======================================================================
async function loadAccounts() {
  const tbody = $("accountsTable").querySelector("tbody");
  tbody.innerHTML = "";
  try {
    const { accounts } = await api("GET", "/api/accounts");
    for (const a of accounts) {
      const tr = document.createElement("tr");
      const canDelete = a.username !== me.username;
      tr.innerHTML =
        `<td>${a.username}</td>` +
        `<td>${a.is_admin ? "admin" : "user"}</td>` +
        `<td>${a.is_admin ? "—" : renderGrants(a.grants)}</td>` +
        `<td>${canDelete ? `<button class="link-danger" data-acct="${a.username}">delete</button>` : ""}</td>`;
      tbody.appendChild(tr);
    }
  } catch (err) { setStatus("accountsStatus", err.message, "error"); }
}

// Fill a namespace <select> with the namespaces that currently exist.
// `wildcard` adds an "all namespaces" (*) option at the top (for API keys).
function fillNsSelect(id, { wildcard = false } = {}) {
  const sel = $(id);
  if (!sel) return;
  const prev = sel.value;
  sel.innerHTML = "";
  if (!knownNamespaces.length && !wildcard) {
    sel.innerHTML = '<option value="">(create a namespace first)</option>';
    return;
  }
  if (wildcard) {
    const o = document.createElement("option");
    o.value = "*"; o.textContent = "all namespaces";
    sel.appendChild(o);
  }
  for (const ns of knownNamespaces) {
    const o = document.createElement("option");
    o.value = o.textContent = ns;
    sel.appendChild(o);
  }
  if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
}

function populateNsSelect() {
  fillNsSelect("accNs");
  fillNsSelect("keyNs", { wildcard: true });
}

// A global admin needs no namespace scope, so hide it when the box is ticked.
$("accAdmin").addEventListener("change", () => {
  $("accScope").style.display = $("accAdmin").checked ? "none" : "";
});

$("accountForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const isAdmin = $("accAdmin").checked;
    let grants = {};
    if (!isAdmin) {
      const ns = $("accNs").value;
      if (!ns) throw new Error("Create a namespace first, then assign the user to it.");
      grants = { [ns]: $("accRole").value };
    }
    await api("POST", "/api/accounts", {
      username: $("accUser").value.trim(),
      password: $("accPass").value,
      grants, is_admin: isAdmin,
    });
    $("accUser").value = $("accPass").value = "";
    $("accAdmin").checked = false;
    $("accScope").style.display = "";
    setStatus("accountsStatus", "Account created.", "ok");
    loadAccounts();
  } catch (err) { setStatus("accountsStatus", err.message, "error"); }
});

async function loadKeys() {
  const tbody = $("keysTable").querySelector("tbody");
  tbody.innerHTML = "";
  try {
    const { keys } = await api("GET", "/api/keys");
    for (const k of keys) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td>${k.name}</td><td>${renderGrants(k.grants)}</td>` +
        `<td>${k.owner || "—"}</td><td><code>${k.prefix}…</code></td>` +
        `<td><button class="link-danger" data-key="${k.id}" data-adminkey="1">revoke</button></td>`;
      tbody.appendChild(tr);
    }
  } catch (err) { setStatus("adminStatus", err.message, "error"); }
}

$("keyForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const ns = $("keyNs").value;
    if (!ns) throw new Error("Create a namespace first, or choose 'all namespaces'.");
    const created = await api("POST", "/api/keys", {
      name: $("keyName").value.trim(), grants: { [ns]: $("keyRole").value },
    });
    const tok = $("newToken");
    tok.hidden = false;
    tok.textContent = `New token for "${created.name}" (copy now — shown once): ${created.token}`;
    $("keyName").value = "";
    setStatus("adminStatus", "");
    loadKeys();
  } catch (err) { setStatus("adminStatus", err.message, "error"); }
});

// ======================================================================
//  Delegated clicks (namespace open + all the delete/revoke buttons)
// ======================================================================
document.addEventListener("click", async (ev) => {
  const t = ev.target;
  if (t.matches(".ns")) { openNamespace(t.dataset.ns); return; }
  if (t.matches(".pill-btn")) { openTable(t.dataset.table); return; }

  // Edit a row.
  if (t.dataset.editrow) { openRowForm(t.dataset.editrow); return; }
  // Delete a row.
  if (t.dataset.delrow) {
    try {
      await api("DELETE", `/api/${enc(currentNs)}/${enc(currentTable)}/rows/${enc(t.dataset.delrow)}`);
      setStatus("status", "Row deleted.", "ok");
      await loadRows();
    } catch (err) { setStatus("status", err.message, "error"); }
    return;
  }
  // Admin: revoke standalone key.
  if (t.dataset.adminkey) {
    try { await api("DELETE", `/api/keys/${enc(t.dataset.key)}`); loadKeys(); }
    catch (err) { setStatus("adminStatus", err.message, "error"); }
    return;
  }
  // My key revoke.
  if (t.dataset.mykey) {
    try { await api("DELETE", `/api/me/keys/${enc(t.dataset.mykey)}`); loadMyKeys(); }
    catch (err) { setStatus("myKeysStatus", err.message, "error"); }
    return;
  }
  // Admin: delete account.
  if (t.dataset.acct) {
    try { await api("DELETE", `/api/accounts/${enc(t.dataset.acct)}`); loadAccounts(); }
    catch (err) { setStatus("accountsStatus", err.message, "error"); }
    return;
  }
});

// Kick things off.
refresh();
