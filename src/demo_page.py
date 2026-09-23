"""A browser page over the local API, for demonstrating single tickets.

    python -m src.api --backend hybrid --open      # then http://127.0.0.1:8000/demo

Everything on the page goes through `POST /tickets`, the same endpoint and the
same pipeline as any other client, so what it shows is what the API decides.
It adds three read-only helpers:

* `GET /demo/samples` - validation tickets with rehearsed outcomes, stripped of
  their evaluation labels (the API rejects labels anyway).
* `GET /demo/docs` - documentation titles, so a cited ID can be read by name.
* `GET /demo/recent` - the latest decisions in the log: ID, time, action and
  rule only. No ticket text leaves the log through this route.

Two things it deliberately does not do. It offers no pause button: the pause is
operated from the command line on this machine, because the README commits to
having no remote administration endpoint, and a button on a web page would be
one. And it cannot show a guardrail block: offline generation never trips one,
so the block is demonstrated in step 3 of scripts/demo.py instead of being
faked here. The page is self-contained and makes no external requests, so it
works with the network off.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "Capstone_Pack/05_Datasets/validation_tickets.json"

# The outcome each sample produced when the demo was rehearsed with hybrid
# retrieval. Shown on the button as what to expect, never used to decide.
SAMPLES = [
    ("VAL-0006", "Answered", "MFA code rejected"),
    ("VAL-0005", "Answered", "API cursor expiring"),
    ("VAL-0010", "Answered", "Invoice higher than expected"),
    ("VAL-0043", "Escalated", "Credentials exposed in a breach"),
    ("VAL-0001", "Escalated", "Exposed production key"),
    ("VAL-0029", "Escalated", "Onboarding, no matching docs"),
]
CUSTOMER_FIELDS = ("ticket_id", "channel", "subject", "body", "received_at", "customer_id",
                   "customer_tier", "customer_region", "language_fluency")


def _samples() -> list[dict]:
    try:
        tickets = {t["ticket_id"]: t for t in json.loads(VALIDATION.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return []
    out = []
    for ticket_id, expect, title in SAMPLES:
        t = tickets.get(ticket_id)
        if not t:
            continue
        # Strip labels and history; send only what a customer's ticket carries.
        ticket = {k: str(t.get(k) or "") for k in CUSTOMER_FIELDS}
        out.append({"expect": expect, "title": title, "ticket": ticket})
    return out


def register(app: FastAPI, *, config, corpus_path: str) -> None:
    @app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
    def demo_page():
        return HTMLResponse(PAGE)

    @app.get("/demo/samples", tags=["demo"])
    def demo_samples():
        return _samples()

    @app.get("/demo/docs", tags=["demo"])
    def demo_docs():
        try:
            docs = json.loads(Path(corpus_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {d["doc_id"]: d.get("title", "") for d in docs}

    @app.get("/demo/recent", tags=["demo"])
    def demo_recent(limit: int = Query(8, ge=1, le=50)):
        path = Path(config.sqlite_path)
        if not path.exists():
            return []
        try:
            with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
                rows = conn.execute(
                    "SELECT id, logged_at, ticket_id, channel, action, rule FROM decisions "
                    "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        except sqlite3.Error:
            return []
        keys = ("id", "logged_at", "ticket_id", "channel", "action", "rule")
        return [dict(zip(keys, r)) for r in rows]


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CloudServe triage · demo</title>
<style>
  :root {
    --ink: #14213d; --muted: #5b6475; --line: #dde2ea; --panel: #ffffff; --ground: #f3f5f9;
    --navy: #1b2a4a; --accent: #e8743b;
    --ok: #137a3f; --ok-bg: #e6f4ec; --warn: #9a5b00; --warn-bg: #fff3dc; --bad: #b3261e; --bad-bg: #fde8e7;
    --mono: ui-monospace, "Cascadia Mono", Consolas, Menlo, monospace;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--ground); color: var(--ink);
         font: 17px/1.5 "Segoe UI", system-ui, -apple-system, Roboto, sans-serif; }
  header { background: var(--navy); color: #fff; padding: 18px 28px; display: flex;
           align-items: center; gap: 24px; flex-wrap: wrap; }
  header h1 { font-size: 22px; margin: 0; font-weight: 650; letter-spacing: .2px; }
  header h1 span { color: var(--accent); }
  .chips { display: flex; gap: 10px; flex-wrap: wrap; margin-left: auto; }
  .chip { font-size: 14px; padding: 5px 12px; border-radius: 99px; background: rgba(255,255,255,.12); }
  .chip b { font-weight: 650; }
  .chip.ok b { color: #7ee2a4; } .chip.bad b { color: #ff9d95; } .chip.warn b { color: #ffd479; }
  main { display: grid; grid-template-columns: minmax(320px, 420px) 1fr; gap: 22px; padding: 22px 28px;
         max-width: 1500px; margin: 0 auto; }
  @media (max-width: 960px) { main { grid-template-columns: 1fr; } }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 18px 20px; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 1.2px; color: var(--muted); margin: 0 0 12px; }
  .samples { display: grid; gap: 8px; margin-bottom: 18px; }
  .sample { text-align: left; border: 1px solid var(--line); background: #fbfcfe; border-radius: 9px;
            padding: 9px 12px; cursor: pointer; font: inherit; font-size: 15px; color: var(--ink);
            display: flex; justify-content: space-between; gap: 10px; align-items: center; }
  .sample:hover { border-color: var(--navy); background: #fff; }
  .sample small { color: var(--muted); font-family: var(--mono); font-size: 12.5px; }
  .tag { font-size: 12px; font-weight: 650; padding: 2px 9px; border-radius: 99px; white-space: nowrap; }
  .tag.Answered { background: var(--ok-bg); color: var(--ok); }
  .tag.Escalated { background: var(--warn-bg); color: var(--warn); }
  label { display: block; font-size: 13px; font-weight: 600; color: var(--muted); margin: 12px 0 5px; }
  input, select, textarea { width: 100%; font: inherit; font-size: 16px; padding: 9px 11px;
         border: 1px solid var(--line); border-radius: 8px; background: #fff; color: var(--ink); }
  textarea { min-height: 150px; resize: vertical; }
  .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  button.go { margin-top: 16px; width: 100%; background: var(--navy); color: #fff; border: 0;
              border-radius: 9px; padding: 13px; font: inherit; font-weight: 650; font-size: 17px; cursor: pointer; }
  button.go:hover { background: #24375f; } button.go:disabled { opacity: .6; cursor: progress; }
  .hint { font-size: 13px; color: var(--muted); margin-top: 8px; }
  .empty { color: var(--muted); text-align: center; padding: 70px 20px; }
  .banner { border-radius: 10px; padding: 16px 20px; display: flex; align-items: baseline; gap: 16px;
            flex-wrap: wrap; margin-bottom: 16px; }
  .banner .verdict { font-size: 30px; font-weight: 750; letter-spacing: .5px; }
  .banner .rule { font-family: var(--mono); font-size: 15px; opacity: .85; }
  .banner.answered { background: var(--ok-bg); color: var(--ok); }
  .banner.escalated { background: var(--warn-bg); color: var(--warn); }
  .banner.blocked { background: var(--bad-bg); color: var(--bad); }
  dl { display: grid; grid-template-columns: 130px 1fr; gap: 10px 16px; margin: 0; }
  dt { font-weight: 650; color: var(--muted); font-size: 14px; padding-top: 2px; }
  dd { margin: 0; }
  .doc { display: inline-block; font-family: var(--mono); font-size: 13px; background: #eef2f8;
         color: var(--navy); border-radius: 6px; padding: 1px 7px; margin: 1px 0; }
  .doc.cited { background: var(--navy); color: #fff; }
  .src li { margin: 3px 0; } .src { margin: 0; padding-left: 0; list-style: none; }
  .reply { white-space: pre-wrap; background: #fbfcfe; border: 1px solid var(--line); border-left: 4px solid var(--ok);
           border-radius: 8px; padding: 14px 16px; max-height: 280px; overflow: auto; font-size: 16px; }
  .reply.agent { border-left-color: var(--warn); color: #3b4252; }
  .withheld { background: var(--bad-bg); color: var(--bad); border-radius: 8px; padding: 12px 16px; font-weight: 650; }
  .pass { color: var(--ok); font-weight: 650; } .fail { color: var(--bad); font-weight: 650; }
  .note { font-size: 13.5px; color: var(--muted); margin-top: 6px; }
  table { width: 100%; border-collapse: collapse; font-size: 14.5px; }
  th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--line); }
  th { font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); }
  td.mono { font-family: var(--mono); font-size: 13px; }
  .a-answered { color: var(--ok); font-weight: 650; } .a-escalated { color: var(--warn); font-weight: 650; }
  .a-blocked { color: var(--bad); font-weight: 650; }
  .stack { display: grid; gap: 22px; align-content: start; }
  .err { background: var(--bad-bg); color: var(--bad); border-radius: 8px; padding: 12px 16px; }
</style>
</head>
<body>
<header>
  <h1>CloudServe <span>support triage</span></h1>
  <div class="chips" id="chips"><span class="chip">connecting…</span></div>
</header>
<main>
  <section class="card">
    <h2>Rehearsed tickets</h2>
    <div class="samples" id="samples"></div>
    <h2>Ticket</h2>
    <form id="form">
      <div class="row2">
        <div><label for="ticket_id">Ticket ID</label><input id="ticket_id" required value="DEMO-1"></div>
        <div><label for="channel">Channel</label>
          <select id="channel"><option>email</option><option>chat</option>
            <option>docs_comment</option><option>forum</option></select></div>
      </div>
      <label for="subject">Subject</label><input id="subject">
      <label for="body">Message</label><textarea id="body" required></textarea>
      <button class="go" id="go" type="submit">Triage this ticket</button>
      <div class="hint">Ctrl+Enter to submit. Sent to <code>POST /tickets</code> on this machine only.</div>
    </form>
  </section>
  <div class="stack">
    <section class="card" id="result"><div class="empty">Pick a rehearsed ticket or write one, then triage it.</div></section>
    <section class="card">
      <h2>Decision log · latest entries</h2>
      <table><thead><tr><th>#</th><th>Time</th><th>Ticket</th><th>Channel</th><th>Action</th><th>Rule</th></tr></thead>
      <tbody id="recent"><tr><td colspan="6" class="note">No decisions yet.</td></tr></tbody></table>
    </section>
  </div>
</main>
<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let DOCS = {};
let META = {};

async function health() {
  try {
    const h = await (await fetch("/health")).json();
    const chip = (cls, label, val) => `<span class="chip ${cls}">${label} <b>${val}</b></span>`;
    $("chips").innerHTML =
      chip(h.semantic_retrieval_available ? "ok" : "bad", "Semantic retrieval", h.semantic_retrieval_available ? "ON" : "OFF (lexical fallback)") +
      chip("", "Generation", h.generation === "provider" ? "live model" : "offline extractive") +
      chip(h.automatic_replies_paused ? "warn" : "ok", "Automatic replies", h.automatic_replies_paused ? "PAUSED" : "ENABLED");
  } catch { $("chips").innerHTML = '<span class="chip bad"><b>API not reachable</b></span>'; }
}

// The log stores UTC; show the time on the presenter's clock.
function localTime(iso) {
  const d = new Date(iso);
  return isNaN(d) ? String(iso || "").slice(0, 19) : d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
}

async function recent() {
  const rows = await (await fetch("/demo/recent?limit=8")).json();
  $("recent").innerHTML = rows.length ? rows.map(r => `<tr><td class="mono">${r.id}</td>
    <td class="mono">${esc(localTime(r.logged_at))}</td>
    <td class="mono">${esc(r.ticket_id)}</td><td>${esc(r.channel)}</td>
    <td class="a-${esc(r.action)}">${esc(r.action)}</td><td class="mono">${esc(r.rule)}</td></tr>`).join("")
    : '<tr><td colspan="6" class="note">No decisions yet.</td></tr>';
}

function withCitations(text, cited) {
  return esc(text).replace(/\[(DOC-[A-Z]+-\d+)\]/g, (m, id) =>
    `<span class="doc cited" title="${esc(DOCS[id] || "")}">${id}</span>`);
}

function render(r) {
  const verdict = {answered: "ANSWERED", escalated: "ESCALATED", blocked: "BLOCKED"}[r.action] || r.action;
  const conf = (r.confidence === null || r.confidence === undefined) ? null : (r.confidence * 100).toFixed(2) + "%";
  const cited = new Set(r.citations || []);
  const sources = (r.sources || []).map(d => `<li><span class="doc ${cited.has(d) ? "cited" : ""}">${esc(d)}</span> ${esc(DOCS[d] || "")}</li>`).join("");
  const guard = (r.guardrail_findings || []).length
    ? r.guardrail_findings.map(f => `<div class="fail">✖ ${esc(f)}</div>`).join("")
    : r.action === "answered" ? '<span class="pass">✔ all checks passed</span>'
    : r.agent_draft ? '<span class="pass">✔ the draft passed every check</span> <span class="note">(escalation drafts are validated too; a person still decides)</span>'
    : '<span class="note">no draft to check</span>';
  let reply;
  if (r.customer_response) {
    reply = `<div class="reply">${withCitations(r.customer_response)}</div>`;
  } else if (r.agent_draft) {
    reply = `<div class="reply agent">${withCitations(r.agent_draft)}</div><div class="note">A draft for the person picking this up. Never sent to the customer.</div>`;
  } else if (r.action === "blocked") {
    reply = '<div class="withheld">Nothing released. The draft was withheld by a guardrail.</div>';
  } else {
    reply = '<div class="note">The ticket, the reason and the retrieved sources go to a person.</div>';
  }
  $("result").innerHTML = `
    <div class="banner ${esc(r.action)}"><span class="verdict">${verdict}</span><span class="rule">rule: ${esc(r.rule)}</span></div>
    <dl>
      <dt>Read as</dt><dd>${r.rule === "automation_paused" ? '<span class="note">not classified: automatic replies are paused</span>'
        : `${esc(r.intent || "?")}${conf ? ` · confidence <b>${conf}</b>` : ""}${r.urgency ? ` · urgency ${esc(r.urgency)}` : ""}`}</dd>
      <dt>Why</dt><dd>${esc(r.reason)}</dd>
      <dt>Retrieved</dt><dd>${sources ? `<ul class="src">${sources}</ul>` : '<span class="note">nothing relevant enough; retrieval abstained</span>'}</dd>
      <dt>Guardrails</dt><dd>${guard}</dd>
      <dt>${r.customer_response ? "To customer" : r.agent_draft ? "For the agent" : "Reply"}</dt><dd>${reply}</dd>
      <dt>Logged</dt><dd class="note">run ${esc(r.run_id)}${r.latency_ms !== undefined ? ` · ${Math.round(r.latency_ms)} ms` : ""}${r.degraded ? ' · <b>degraded</b>: model failed, extractive fallback used' : ""}</dd>
    </dl>`;
}

async function submit(ev) {
  ev && ev.preventDefault();
  const payload = {ticket_id: $("ticket_id").value.trim(), channel: $("channel").value,
                   subject: $("subject").value, body: $("body").value, ...META};
  $("go").disabled = true; $("go").textContent = "Triaging…";
  try {
    const res = await fetch("/tickets", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    const data = await res.json();
    if (!res.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map(d => `${(d.loc || []).slice(-1)[0]}: ${d.msg}`).join("; ") : data.detail;
      $("result").innerHTML = `<div class="err"><b>${res.status}</b> ${esc(detail)}</div>`;
    } else { render(data); }
  } catch (e) {
    $("result").innerHTML = `<div class="err">Could not reach the API: ${esc(e)}</div>`;
  } finally {
    $("go").disabled = false; $("go").textContent = "Triage this ticket";
    recent(); health();
  }
}

async function init() {
  health();
  DOCS = await (await fetch("/demo/docs")).json();
  const samples = await (await fetch("/demo/samples")).json();
  $("samples").innerHTML = samples.map((s, i) => `<button class="sample" data-i="${i}">
      <span>${esc(s.title)}<br><small>${esc(s.ticket.ticket_id)} · ${esc(s.ticket.channel)}</small></span>
      <span class="tag ${esc(s.expect)}">${esc(s.expect)}</span></button>`).join("");
  document.querySelectorAll(".sample").forEach(b => b.addEventListener("click", () => {
    const t = samples[+b.dataset.i].ticket;
    $("ticket_id").value = t.ticket_id; $("channel").value = t.channel;
    $("subject").value = t.subject; $("body").value = t.body;
    META = {customer_tier: t.customer_tier, customer_region: t.customer_region, language_fluency: t.language_fluency};
    submit();
  }));
  ["subject", "body"].forEach(id => $(id).addEventListener("input", () => { META = {}; }));
  $("form").addEventListener("submit", submit);
  $("body").addEventListener("keydown", e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit(e); });
  recent();
  setInterval(health, 5000);
}
init();
</script>
</body>
</html>
"""
