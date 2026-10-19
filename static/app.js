"use strict";

if (!location.pathname.endsWith("/")) location.replace(location.pathname + "/" + location.search + location.hash);

const $ = (s) => document.querySelector(s);
const STATUS = { waiting: "Needs you", busy: "Working", idle: "Idle", shell: "Shell running", unknown: "Unknown" };

const state = { sessions: [], presets: {}, accounts: [], current: null, sid: null, next: 0, items: [], tools: new Map(), screen: null };
let timers = [];

async function api(path, body, headers = {}) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "X-Bridge": "1", ...(body instanceof Blob ? {} : { "Content-Type": "application/json" }), ...headers },
    body: body instanceof Blob ? body : JSON.stringify(body),
  };
  const res = await fetch("api/" + path, opts);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
  return res.json();
}

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null) n.append(kid);
  return n;
}

function ago(ms) {
  if (!ms) return "";
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + " min ago";
  if (s < 86400) return Math.floor(s / 3600) + " h ago";
  return Math.floor(s / 86400) + " d ago";
}

function row(s) {
  const active = s.status !== "idle" && s.status !== "unknown";
  return el("button", { class: "row", onclick: () => openChat(s.id) },
    el("span", { class: "dot " + s.status }),
    el("span", { class: "name" }, s.name),
    el("span", { class: "when" }, ago(s.updatedAt)),
    el("span", {}),
    el("span", { class: "title" }, s.title || s.cwd || ""),
    active ? el("span", {}) : null,
    active ? el("span", { class: "state " + s.status }, STATUS[s.status] || s.status) : null);
}

function renderList() {
  const groups = $("#groups");
  groups.replaceChildren();
  const waiting = state.sessions.filter((s) => s.status === "waiting");
  if (waiting.length) groups.append(el("section", { class: "group", "data-account": "needs" }, el("h2", {}, "Needs you"), waiting.map(row)));
  const order = state.accounts.map((a) => a.id);
  const accounts = [...new Set(state.sessions.map((s) => s.account))].sort((a, b) => (order.indexOf(a) + 99) % 99 - (order.indexOf(b) + 99) % 99);
  for (const a of accounts) {
    const list = state.sessions.filter((s) => s.account === a && s.status !== "waiting");
    if (!list.length) continue;
    const i = order.indexOf(a);
    const info = state.accounts[i] || {};
    const section = el("section", { class: "group" }, el("h2", {}, info.label || a), list.map(row));
    if (i >= 0) section.style.setProperty("--acc", info.color || `var(--acct-${(i % 3) + 1})`);
    groups.append(section);
  }
  $("#empty").hidden = state.sessions.length > 0;
}

async function loadSessions() {
  try {
    const data = await api("sessions");
    state.sessions = data.sessions;
    state.presets = data.presets;
    state.accounts = data.accounts || [];
    $("#list-error").hidden = true;
    if (state.current) updateHeader();
    else renderList();
  } catch (e) {
    $("#list-error").textContent = "Can't reach the server: " + e.message;
    $("#list-error").hidden = false;
  }
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function inline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

function markdown(src) {
  const out = [];
  const parts = escapeHtml(src).split(/^```[^\n]*\n?/m);
  parts.forEach((part, i) => {
    if (i % 2) { out.push("<pre><code>" + part.replace(/\n$/, "") + "</code></pre>"); return; }
    for (const block of part.split(/\n{2,}/)) {
      if (!block.trim()) continue;
      if (block.trim().startsWith("|")) { out.push("<pre>" + block.trim() + "</pre>"); continue; }
      let para = [], list = null;
      const flushPara = () => { if (para.length) out.push("<p>" + para.join("<br>") + "</p>"); para = []; };
      const flushList = () => { if (list) out.push(`<${list.tag}>` + list.items.map((i) => "<li>" + i + "</li>").join("") + `</${list.tag}>`); list = null; };
      for (const l of block.split("\n")) {
        const h = l.match(/^#{1,6}\s+(.*)$/);
        const ul = l.match(/^\s*[-*•]\s+(.*)$/);
        const ol = l.match(/^\s*\d+[.)]\s+(.*)$/);
        if (h) { flushPara(); flushList(); out.push('<span class="h">' + inline(h[1]) + "</span>"); }
        else if (ul || ol) {
          flushPara();
          const tag = ul ? "ul" : "ol";
          if (list && list.tag !== tag) flushList();
          if (!list) list = { tag, items: [] };
          list.items.push(inline((ul || ol)[1]));
        } else if (list && /^\s{2,}\S/.test(l)) {
          list.items[list.items.length - 1] += "<br>" + inline(l.trim());
        } else { flushList(); para.push(inline(l)); }
      }
      flushPara();
      flushList();
    }
  });
  return out.join("");
}

function renderItem(it) {
  if (it.k === "user") return el("div", { class: "msg user" }, it.text);
  if (it.k === "claude") {
    const n = el("div", { class: "msg claude" });
    n.innerHTML = markdown(it.text);
    return n;
  }
  if (it.k === "tool") {
    const result = el("pre", { hidden: true });
    const n = el("details", { class: "tool pending" },
      el("summary", {}, el("b", {}, it.name), el("span", {}, it.summary.split("\n")[0])),
      el("pre", { class: "cmd" }, it.summary), result);
    state.tools.set(it.id, { n, result });
    return n;
  }
  if (it.k === "result") {
    const t = state.tools.get(it.for);
    if (t) {
      t.n.classList.remove("pending");
      if (it.error) t.n.classList.add("error");
      t.result.textContent = it.text || "(no output)";
      t.result.hidden = false;
    }
    return null;
  }
  return null;
}

function nearBottom() {
  return window.innerHeight + window.scrollY >= document.body.scrollHeight - 120;
}

async function loadMessages() {
  const s = state.current;
  if (!s) return;
  const q = state.sid ? `?sid=${encodeURIComponent(state.sid)}&after=${state.next}` : "";
  let data;
  try { data = await api(`sessions/${s}/messages${q}`); } catch { return; }
  if (s !== state.current) return;
  const stick = nearBottom() || state.sid === null;
  const log = $("#log");
  if (data.sid !== state.sid || data.start !== state.next) {
    log.replaceChildren();
    state.tools.clear();
    if (data.start > 0) log.append(el("p", { class: "muted more" }, `Showing the last ${data.items.length} entries`));
  }
  state.sid = data.sid;
  state.next = data.next;
  for (const it of data.items) {
    const n = renderItem(it);
    if (n) log.append(n);
  }
  if (stick && data.items.length) window.scrollTo(0, document.body.scrollHeight);
}

function currentSession() {
  return state.sessions.find((x) => x.id === state.current);
}

function updateHeader() {
  const s = currentSession();
  if (!s) { $("#chat-sub").textContent = "This session has ended"; $("#prompt").hidden = true; return; }
  $("#chat-name").textContent = s.name;
  $("#chat-sub").textContent = [STATUS[s.status] || s.status, s.title].filter(Boolean).join(", ");
  $("#chat-sub").className = "state " + s.status;
  $("#attached").hidden = !s.attached;
}

async function loadScreen() {
  const s = currentSession();
  const peekOpen = !$("#peek").hidden;
  if (!s || (s.status !== "waiting" && !peekOpen)) { $("#prompt").hidden = true; return; }
  let data;
  try { data = await api(`sessions/${s.id}/screen`); } catch { return; }
  if (peekOpen) $("#peek").textContent = data.text;
  const waiting = data.status === "waiting" && data.options.length > 0;
  $("#prompt").hidden = !waiting;
  if (!waiting) return;
  const lines = data.text.split("\n");
  const first = lines.findIndex((l) => /^\s*(?:[❯›>]\s*)?\d\.\s/.test(l) && data.options.some((o) => l.includes(o.label)));
  const question = lines.slice(Math.max(0, first - 14), first).join("\n").replace(/^[\s─━-]+$/gm, "").trim();
  $("#prompt-text").textContent = question;
  $("#prompt-options").replaceChildren(...data.options.map((o) =>
    el("button", { class: "btn" + (o.key === data.selected ? " sel" : ""), onclick: () => sendKey(o.key) }, `${o.key}. ${o.label}`)));
}

async function sendKey(key) {
  try { await api(`sessions/${state.current}/key`, { key }); } catch (e) { alertInline(e.message); }
  setTimeout(() => { loadSessions(); loadScreen(); }, 400);
}

function alertInline(msg) {
  const n = el("p", { class: "error" }, msg);
  $("#log").append(n);
  window.scrollTo(0, document.body.scrollHeight);
}

function stopTimers() { timers.forEach(clearInterval); timers = []; }

function every(ms, fn) { fn(); timers.push(setInterval(() => { if (!document.hidden) fn(); }, ms)); }

function openChat(id, push = true) {
  stopTimers();
  Object.assign(state, { current: id, sid: null, next: 0 });
  state.tools.clear();
  $("#log").replaceChildren();
  $("#list").hidden = true;
  $("#chat").hidden = false;
  $("#prompt").hidden = true;
  updateHeader();
  if (push) history.pushState({ id }, "", "#" + id);
  every(1500, loadMessages);
  every(3000, loadSessions);
  every(2000, loadScreen);
}

function openList(push = true) {
  stopTimers();
  state.current = null;
  $("#chat").hidden = true;
  $("#list").hidden = false;
  if (push) history.pushState({}, "", location.pathname);
  every(3000, loadSessions);
}

$("#back").addEventListener("click", () => openList());
window.addEventListener("popstate", () => {
  const id = location.hash.slice(1);
  if (id) openChat(id, false); else openList(false);
});

$("#peek-btn").addEventListener("click", () => {
  const p = $("#peek");
  p.hidden = !p.hidden;
  $("#peek-btn").setAttribute("aria-pressed", String(!p.hidden));
  loadScreen();
});

$("#keys-btn").addEventListener("click", () => {
  const k = $("#keys");
  k.hidden = !k.hidden;
  $("#keys-btn").setAttribute("aria-pressed", String(!k.hidden));
});
document.querySelectorAll("#keys [data-key]").forEach((b) => b.addEventListener("click", () => sendKey(b.dataset.key)));

const input = $("#input");
function grow() { input.style.height = "auto"; input.style.height = input.scrollHeight + "px"; }
input.addEventListener("input", grow);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); }
});

$("#composer").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  $("#send").disabled = true;
  try {
    await api(`sessions/${state.current}/send`, { text });
    input.value = "";
    grow();
    setTimeout(loadMessages, 600);
  } catch (err) {
    alertInline("Not sent: " + err.message);
  } finally {
    $("#send").disabled = false;
  }
});

$("#file").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (!f) return;
  try {
    const { path } = await api(`sessions/${state.current}/upload`, f, { "X-Filename": f.name });
    input.value = (input.value ? input.value.trimEnd() + "\n" : "") + path + " ";
    grow();
    input.focus();
  } catch (err) {
    alertInline("Upload failed: " + err.message);
  }
});

$("#new-btn").addEventListener("click", () => {
  $("#presets").replaceChildren(...Object.entries(state.presets).map(([k, label]) =>
    el("button", { class: "btn", value: k, onclick: (ev) => { ev.preventDefault(); startSession(k, label); } }, label)));
  $("#new-dialog").showModal();
});

async function startSession(preset, label) {
  const buttons = [...document.querySelectorAll("#presets .btn")];
  buttons.forEach((b) => (b.disabled = true));
  const h = $("#new-dialog h2");
  h.textContent = `Starting Claude in ${label}…`;
  try {
    const { pane } = await api("new", { preset });
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      await loadSessions();
      if (state.sessions.some((s) => s.id === pane)) { $("#new-dialog").close(); openChat(pane); return; }
    }
    h.textContent = "The window opened, but Claude hasn't shown up yet. Check it in Termius.";
  } catch (e) {
    h.textContent = "Couldn't start: " + e.message;
  } finally {
    buttons.forEach((b) => (b.disabled = false));
    $("#new-dialog").addEventListener("close", () => (h.textContent = "Start a session in"), { once: true });
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  loadSessions();
  if (state.current) { loadMessages(); loadScreen(); }
});

const start = location.hash.slice(1);
if (start) openChat(start, false); else openList(false);
