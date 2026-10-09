"use strict";

let currentNs = null;
let me = { authenticated: false, is_admin: false, username: null };
let knownNamespaces = [];

const $ = (id) => document.getElementById(id);
const authOn = () => document.body.dataset.auth === "on";

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
function parseValue(type, raw) {
  switch (type) {
    case "string": return raw;
    case "boolean": return raw.trim().toLowerCase() === "true";
    case "number": {
      const n = Number(raw);
      if (Number.isNaN(n)) throw new Error("Invalid number.");
      return n;
    }
    case "array": {
      const v = JSON.parse(raw);
      if (!Array.isArray(v)) throw new Error("Value must be a JSON array.");
      return v;
    }
    default: return raw;
  }
}
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
  $("currentNs").textContent = ns;
  $("entryForm").hidden = false;
  $("toolbar").hidden = false;
  await refreshNamespaces();
  await loadEntries();
}

async function loadEntries() {
  const table = $("entries");
  const tbody = table.querySelector("tbody");
  tbody.innerHTML = "";
  try {
    const { entries } = await api("GET", `/api/${encodeURIComponent(currentNs)}`);
    $("emptyMsg").hidden = entries.length > 0;
    table.hidden = entries.length === 0;
    for (const e of entries) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td><code>${e.key}</code></td>` +
        `<td>${e.type}</td>` +
        `<td>${renderValue(e.value)}</td>` +
        `<td>${(e.updated_at || "").slice(0, 19).replace("T", " ")}</td>` +
        `<td><button class="link-danger" data-key="${e.key}">delete</button></td>`;
      tbody.appendChild(tr);
    }
    setStatus("status", "");
  } catch (err) {
    setStatus("status", err.message, "error");
  }
}

// Create, or update if it already exists (POST collection, PUT on 409).
async function upsertEntry(ns, key, value, type) {
  const nsPath = `/api/${encodeURIComponent(ns)}`;
  try {
    await api("POST", nsPath, { key, value, type });
  } catch (err) {
    if (err.status === 409) {
      await api("PUT", `${nsPath}/${encodeURIComponent(key)}`, { value, type });
    } else {
      throw err;
    }
  }
}

$("newNamespace").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const ns = $("nsInput").value.trim();
  if (!ns) return;
  try {
    await api("POST", "/api/namespaces", { name: ns });
    $("nsInput").value = "";
    await openNamespace(ns);
  } catch (err) {
    setStatus("status", err.message, "error");
  }
});

$("entryForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const key = $("keyInput").value.trim();
  const type = $("typeInput").value;
  try {
    const value = parseValue(type, $("valueInput").value);
    await upsertEntry(currentNs, key, value, type);
    $("keyInput").value = $("valueInput").value = "";
    setStatus("status", `Saved "${key}".`, "ok");
    loadEntries();
    refreshNamespaces();
  } catch (err) {
    setStatus("status", err.message, "error");
  }
});

// ======================================================================
//  CSV (web-UI only; built on the entry API)
// ======================================================================
function toCsv(entries) {
  const esc = (s) => (/[",\n]/.test(s) ? '"' + String(s).replace(/"/g, '""') + '"' : s);
  const enc = (v, t) => (t === "string" ? v : t === "array" ? JSON.stringify(v) : String(v));
  const lines = ["key,type,value"];
  for (const e of entries) lines.push([esc(e.key), e.type, esc(enc(e.value, e.type))].join(","));
  return lines.join("\n") + "\n";
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
function inferType(raw) {
  const t = raw.trim().toLowerCase();
  if (t === "true" || t === "false") return "boolean";
  if (raw.trim().startsWith("[")) return "array";
  if (raw.trim() !== "" && !Number.isNaN(Number(raw))) return "number";
  return "string";
}
function rowsToEntries(rows) {
  if (!rows.length) return [];
  let start = 0;
  const h = rows[0].map((c) => c.trim().toLowerCase());
  if ((h[0] === "key" && h[1] === "type" && h[2] === "value") || (h[0] === "key" && h[1] === "value")) start = 1;
  const out = [];
  for (let i = start; i < rows.length; i++) {
    const r = rows[i];
    if (!r.length || r.every((c) => !c.trim())) continue;
    const key = r[0];
    const typeCell = r.length >= 3 ? r[1].trim() : "";
    const rawValue = r.length >= 3 ? r[2] : (r[1] ?? "");
    const type = typeCell || inferType(rawValue);
    out.push({ key, type, value: parseValue(type, rawValue) });
  }
  return out;
}
async function downloadCsv() {
  if (!currentNs) return;
  try {
    const { entries } = await api("GET", `/api/${encodeURIComponent(currentNs)}`);
    const blob = new Blob([toCsv(entries)], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `${currentNs}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    setStatus("status", `Downloaded "${currentNs}.csv".`, "ok");
  } catch (err) { setStatus("status", err.message, "error"); }
}
async function importCsv(file) {
  if (!currentNs || !file) return;
  try {
    const entries = rowsToEntries(parseCsvText(await file.text()));
    let n = 0;
    for (const e of entries) { await upsertEntry(currentNs, e.key, e.value, e.type); n++; }
    setStatus("status", `Imported ${n} entr${n === 1 ? "y" : "ies"}.`, "ok");
    loadEntries(); refreshNamespaces();
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

  // Admin: revoke standalone key.
  if (t.dataset.adminkey) {
    try { await api("DELETE", `/api/keys/${encodeURIComponent(t.dataset.key)}`); loadKeys(); }
    catch (err) { setStatus("adminStatus", err.message, "error"); }
    return;
  }
  // My key revoke.
  if (t.dataset.mykey) {
    try { await api("DELETE", `/api/me/keys/${encodeURIComponent(t.dataset.mykey)}`); loadMyKeys(); }
    catch (err) { setStatus("myKeysStatus", err.message, "error"); }
    return;
  }
  // Admin: delete account.
  if (t.dataset.acct) {
    try { await api("DELETE", `/api/accounts/${encodeURIComponent(t.dataset.acct)}`); loadAccounts(); }
    catch (err) { setStatus("accountsStatus", err.message, "error"); }
    return;
  }
  // Entry delete (has data-key but not data-adminkey).
  if (t.matches(".link-danger") && t.dataset.key !== undefined) {
    try {
      await api("DELETE", `/api/${encodeURIComponent(currentNs)}/${encodeURIComponent(t.dataset.key)}`);
      setStatus("status", `Deleted "${t.dataset.key}".`, "ok");
      loadEntries(); refreshNamespaces();
    } catch (err) { setStatus("status", err.message, "error"); }
  }
});

// Kick things off.
refresh();
