"""Engram Web UI — review proposals, lessons, and accuracy stats."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request

import engram.core.db as db
import engram.core.stats as stats_mod

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Inline HTML — single-page app, no build step, no npm
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Engram</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f1f5f9;
      color: #1e293b;
      min-height: 100vh;
    }

    header {
      background: #0f172a;
      color: #f8fafc;
      padding: 0 32px;
      height: 56px;
      display: flex;
      align-items: center;
      gap: 12px;
      position: sticky;
      top: 0;
      z-index: 100;
    }
    header .logo { font-weight: 700; font-size: 18px; letter-spacing: -0.3px; }
    header .system-name {
      font-size: 13px; color: #94a3b8;
      padding: 3px 8px; background: #1e293b; border-radius: 4px;
    }
    header .spacer { flex: 1; }
    header .header-stats { font-size: 13px; color: #64748b; }

    nav {
      background: #fff;
      border-bottom: 1px solid #e2e8f0;
      padding: 0 32px;
      display: flex;
    }
    nav button {
      background: none; border: none;
      padding: 14px 20px; font-size: 14px; font-weight: 500;
      color: #64748b; cursor: pointer;
      border-bottom: 2px solid transparent;
      transition: color 0.15s, border-color 0.15s;
    }
    nav button:hover { color: #1e293b; }
    nav button.active { color: #4f46e5; border-bottom-color: #4f46e5; }
    nav .tab-badge {
      display: inline-flex; align-items: center; justify-content: center;
      background: #ef4444; color: white;
      font-size: 11px; font-weight: 600;
      width: 18px; height: 18px; border-radius: 9px; margin-left: 6px;
    }

    main { max-width: 860px; margin: 0 auto; padding: 32px 24px; }

    .filter-bar {
      display: flex; align-items: center; gap: 8px; margin-bottom: 24px;
    }
    .filter-btn {
      background: #fff; border: 1px solid #e2e8f0;
      padding: 6px 14px; border-radius: 6px;
      font-size: 13px; font-weight: 500; color: #64748b;
      cursor: pointer; transition: all 0.15s;
    }
    .filter-btn:hover { border-color: #94a3b8; color: #1e293b; }
    .filter-btn.active { background: #4f46e5; border-color: #4f46e5; color: #fff; }
    .filter-bar .spacer { flex: 1; }
    .filter-bar .count { font-size: 13px; color: #94a3b8; }

    .proposal-card {
      background: #fff; border: 1px solid #e2e8f0;
      border-radius: 12px; padding: 24px; margin-bottom: 16px;
      transition: box-shadow 0.15s;
    }
    .proposal-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.06); }
    .proposal-card.status-applied { opacity: 0.65; }
    .proposal-card.status-rejected { opacity: 0.5; }

    .card-header {
      display: flex; align-items: center; gap: 8px; margin-bottom: 10px;
    }
    .card-date { font-size: 12px; color: #94a3b8; margin-left: auto; }
    .card-title {
      font-size: 16px; font-weight: 600; color: #0f172a;
      margin-bottom: 16px; line-height: 1.4;
    }

    .badge {
      display: inline-flex; align-items: center;
      padding: 3px 8px; border-radius: 4px;
      font-size: 11px; font-weight: 700;
      letter-spacing: 0.3px; text-transform: uppercase;
    }
    .badge-high     { background: #fef2f2; color: #dc2626; }
    .badge-medium   { background: #fffbeb; color: #d97706; }
    .badge-low      { background: #f8fafc; color: #64748b; }
    .badge-category { background: #eef2ff; color: #4f46e5; }
    .badge-applied  { background: #f0fdf4; color: #16a34a; }
    .badge-rejected { background: #fef2f2; color: #dc2626; }

    .section { margin-bottom: 14px; }
    .section:last-child { margin-bottom: 0; }
    .section-label {
      font-size: 11px; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.5px;
      color: #94a3b8; margin-bottom: 6px;
    }
    .section-text { font-size: 14px; color: #334155; line-height: 1.6; }

    .evidence-list { list-style: none; display: flex; flex-direction: column; gap: 4px; }
    .evidence-list li {
      font-size: 13px; color: #475569;
      padding-left: 14px; position: relative; line-height: 1.5;
    }
    .evidence-list li::before {
      content: "·"; position: absolute; left: 0; color: #94a3b8; font-weight: 700;
    }

    .code-wrap { position: relative; }
    .code-block {
      background: #0f172a; color: #e2e8f0;
      font-family: "SF Mono", "Fira Code", Consolas, monospace;
      font-size: 12.5px; line-height: 1.6;
      padding: 16px; border-radius: 8px;
      overflow-x: auto; white-space: pre;
    }
    .code-toggle {
      background: none; border: 1px solid #e2e8f0;
      border-radius: 6px; padding: 5px 12px;
      font-size: 12px; color: #64748b; cursor: pointer; margin-bottom: 8px;
    }
    .code-toggle:hover { background: #f8fafc; }

    .files { display: flex; flex-wrap: wrap; gap: 6px; }
    .file-tag {
      font-family: "SF Mono", "Fira Code", Consolas, monospace;
      font-size: 12px; background: #f1f5f9; color: #475569;
      padding: 3px 8px; border-radius: 4px; border: 1px solid #e2e8f0;
    }

    .actions {
      margin-top: 20px; padding-top: 20px; border-top: 1px solid #f1f5f9;
    }
    .notes-input {
      width: 100%; padding: 10px 12px;
      border: 1px solid #e2e8f0; border-radius: 8px;
      font-size: 13px; color: #334155;
      resize: vertical; min-height: 44px; max-height: 120px;
      margin-bottom: 12px; font-family: inherit;
      transition: border-color 0.15s;
    }
    .notes-input:focus { outline: none; border-color: #4f46e5; }

    .action-buttons { display: flex; gap: 8px; align-items: center; }
    .btn {
      padding: 8px 20px; border-radius: 8px;
      font-size: 14px; font-weight: 600;
      cursor: pointer; border: none;
      transition: opacity 0.15s, transform 0.1s;
      display: inline-flex; align-items: center; gap: 6px;
    }
    .btn:hover:not(:disabled) { opacity: 0.85; transform: translateY(-1px); }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
    .btn-apply-code { background: #4f46e5; color: #fff; }
    .btn-apply      { background: #10b981; color: #fff; }
    .btn-reject     { background: #f1f5f9; color: #64748b; border: 1px solid #e2e8f0; }

    .apply-result {
      margin-top: 10px; padding: 10px 14px;
      border-radius: 8px; font-size: 13px;
      display: none;
    }
    .apply-result.success { background: #f0fdf4; color: #15803d; border: 1px solid #bbf7d0; }
    .apply-result.error   { background: #fef2f2; color: #dc2626; border: 1px solid #fecaca; }

    .spinner {
      width: 14px; height: 14px;
      border: 2px solid rgba(255,255,255,0.4);
      border-top-color: white;
      border-radius: 50%;
      animation: spin 0.6s linear infinite;
      display: none;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    .status-done {
      display: inline-flex; align-items: center; gap: 6px;
      font-size: 13px; color: #64748b;
      margin-top: 16px; padding-top: 16px; border-top: 1px solid #f1f5f9;
    }

    .lesson-card {
      background: #fff; border: 1px solid #e2e8f0;
      border-radius: 10px; padding: 16px 20px;
      margin-bottom: 10px;
      display: flex; align-items: flex-start; gap: 12px;
    }
    .lesson-dot {
      width: 8px; height: 8px; border-radius: 50%;
      background: #10b981; margin-top: 6px; flex-shrink: 0;
    }
    .lesson-text { font-size: 14px; color: #334155; line-height: 1.6; flex: 1; }
    .lesson-expiry { font-size: 12px; color: #94a3b8; margin-top: 4px; }

    .stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 16px; margin-bottom: 32px;
    }
    .stat-card {
      background: #fff; border: 1px solid #e2e8f0;
      border-radius: 12px; padding: 20px; text-align: center;
    }
    .stat-value { font-size: 32px; font-weight: 700; color: #0f172a; line-height: 1; }
    .stat-label { font-size: 13px; color: #64748b; margin-top: 6px; }
    .stat-card.highlight .stat-value { color: #4f46e5; }

    .breakdown-table { width: 100%; border-collapse: collapse; }
    .breakdown-table th {
      text-align: left; font-size: 12px; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.4px;
      color: #94a3b8; padding: 0 0 10px 0; border-bottom: 1px solid #e2e8f0;
    }
    .breakdown-table td {
      padding: 10px 0; font-size: 14px; color: #334155;
      border-bottom: 1px solid #f8fafc;
    }
    .breakdown-table td:last-child { text-align: right; font-weight: 600; }
    .accuracy-bar {
      height: 6px; background: #f1f5f9;
      border-radius: 3px; margin-top: 3px; overflow: hidden;
    }
    .accuracy-fill { height: 100%; background: #10b981; border-radius: 3px; }

    .empty { text-align: center; padding: 60px 0; color: #94a3b8; font-size: 15px; }
    .section-title {
      font-size: 13px; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.5px;
      color: #94a3b8; margin-bottom: 16px;
    }

    .toast {
      position: fixed; bottom: 24px; right: 24px;
      background: #0f172a; color: #f8fafc;
      padding: 12px 20px; border-radius: 8px;
      font-size: 14px; opacity: 0; transform: translateY(8px);
      transition: opacity 0.2s, transform 0.2s;
      pointer-events: none; z-index: 200;
    }
    .toast.show { opacity: 1; transform: translateY(0); }

    [data-tab] { display: none; }
    [data-tab].active { display: block; }
  </style>
</head>
<body>

<header>
  <span class="logo">Engram</span>
  <span class="system-name" id="system-name">—</span>
  <span class="spacer"></span>
  <span class="header-stats" id="header-stats"></span>
</header>

<nav>
  <button class="active" onclick="switchTab('proposals')">
    Proposals <span class="tab-badge" id="pending-badge" style="display:none">0</span>
  </button>
  <button onclick="switchTab('lessons')">Lessons</button>
  <button onclick="switchTab('stats')">Stats</button>
</nav>

<main>
  <div data-tab="proposals" class="active">
    <div class="filter-bar">
      <button class="filter-btn active" id="filter-pending" onclick="setFilter('pending')">Pending</button>
      <button class="filter-btn" id="filter-all" onclick="setFilter('all')">All</button>
      <span class="spacer"></span>
      <span class="count" id="proposals-count"></span>
    </div>
    <div id="proposals-list"></div>
  </div>

  <div data-tab="lessons">
    <div id="lessons-list"></div>
  </div>

  <div data-tab="stats">
    <div class="stats-grid" id="stats-grid"></div>
    <div class="section-title">Accuracy by decision type</div>
    <table class="breakdown-table">
      <thead><tr><th>Decision</th><th>Correct</th><th>Wrong</th><th>Accuracy</th></tr></thead>
      <tbody id="breakdown-body"></tbody>
    </table>
  </div>
</main>

<div class="toast" id="toast"></div>

<script>
  let currentFilter = 'pending';

  function switchTab(tab) {
    document.querySelectorAll('[data-tab]').forEach(el => el.classList.remove('active'));
    document.querySelector(`[data-tab="${tab}"]`).classList.add('active');
    document.querySelectorAll('nav button').forEach((btn, i) => {
      btn.classList.toggle('active', ['proposals','lessons','stats'][i] === tab);
    });
    if (tab === 'proposals') loadProposals();
    if (tab === 'lessons')   loadLessons();
    if (tab === 'stats')     loadStats();
  }

  function setFilter(f) {
    currentFilter = f;
    document.getElementById('filter-pending').classList.toggle('active', f === 'pending');
    document.getElementById('filter-all').classList.toggle('active', f === 'all');
    loadProposals();
  }

  // -------------------------------------------------------------------------
  // Proposals
  // -------------------------------------------------------------------------
  async function loadProposals() {
    const url = currentFilter === 'all' ? '/api/proposals?all=1' : '/api/proposals';
    try {
      const res = await fetch(url);
      const proposals = await res.json();
      renderProposals(proposals);

      const pending = proposals.filter(p => p.status === 'pending').length;
      const badge = document.getElementById('pending-badge');
      badge.textContent = pending;
      badge.style.display = pending > 0 ? 'inline-flex' : 'none';
      document.getElementById('proposals-count').textContent =
        `${proposals.length} proposal${proposals.length !== 1 ? 's' : ''}`;
    } catch(e) {
      document.getElementById('proposals-list').innerHTML =
        '<div class="empty">Could not load proposals.</div>';
    }
  }

  function renderProposals(proposals) {
    const el = document.getElementById('proposals-list');
    if (!proposals.length) {
      el.innerHTML = '<div class="empty">No proposals.</div>';
      return;
    }
    el.innerHTML = proposals.map(renderProposal).join('');
  }

  function renderProposal(p) {
    const priClass  = p.priority === 'high' ? 'badge-high' : p.priority === 'medium' ? 'badge-medium' : 'badge-low';
    const evidence  = Array.isArray(p.evidence) ? p.evidence : [];
    const cardClass = p.status !== 'pending' ? `status-${p.status}` : '';

    const codeHtml = p.code_change ? `
      <div class="section">
        <div class="section-label">Code change</div>
        <button class="code-toggle" onclick="toggleCode(${p.id})">Show code</button>
        <pre class="code-block" id="code-${p.id}" style="display:none">${esc(p.code_change)}</pre>
      </div>` : '';

    const filesHtml = p.affected_files ? `
      <div class="section">
        <div class="section-label">Affected files</div>
        <div class="files">${p.affected_files.split(',').map(f =>
          `<span class="file-tag">${esc(f.trim())}</span>`).join('')}
        </div>
      </div>` : '';

    let actionsHtml;
    if (p.status === 'pending') {
      const hasFiles = !!p.affected_files;
      actionsHtml = `
        <div class="actions">
          <textarea id="notes-${p.id}" class="notes-input" placeholder="Notes (optional)..."></textarea>
          <div class="action-buttons">
            ${hasFiles ? `
              <button class="btn btn-apply-code" id="btn-apply-code-${p.id}" onclick="applyCode(${p.id})">
                <span class="spinner" id="spinner-${p.id}"></span>
                Apply
              </button>` : `
              <button class="btn btn-apply" onclick="markApplied(${p.id})">Apply</button>`}
            <button class="btn btn-reject" onclick="rejectProposal(${p.id})">Reject</button>
          </div>
          <div class="apply-result" id="result-${p.id}"></div>
        </div>`;
    } else {
      actionsHtml = `
        <div class="status-done">
          <span class="badge ${p.status === 'applied' ? 'badge-applied' : 'badge-rejected'}">${p.status}</span>
          ${p.user_notes ? `<span>${esc(p.user_notes)}</span>` : ''}
        </div>`;
    }

    return `
      <div class="proposal-card ${cardClass}" id="proposal-${p.id}">
        <div class="card-header">
          <span class="badge ${priClass}">${p.priority}</span>
          <span class="badge badge-category">${p.category}</span>
          <span class="card-date">${p.analysis_date}</span>
        </div>
        <div class="card-title">${esc(p.title)}</div>

        <div class="section">
          <div class="section-label">Problem</div>
          <div class="section-text">${esc(p.problem)}</div>
        </div>

        ${evidence.length ? `
        <div class="section">
          <div class="section-label">Evidence</div>
          <ul class="evidence-list">${evidence.map(e => `<li>${esc(e)}</li>`).join('')}</ul>
        </div>` : ''}

        <div class="section">
          <div class="section-label">Proposal</div>
          <div class="section-text">${esc(p.proposal)}</div>
        </div>

        ${codeHtml}
        ${filesHtml}
        ${actionsHtml}
      </div>`;
  }

  function toggleCode(id) {
    const pre = document.getElementById(`code-${id}`);
    const btn = pre.previousElementSibling;
    const hidden = pre.style.display === 'none';
    pre.style.display = hidden ? 'block' : 'none';
    btn.textContent = hidden ? 'Hide code' : 'Show code';
  }

  async function applyCode(id) {
    const btn     = document.getElementById(`btn-apply-code-${id}`);
    const spinner = document.getElementById(`spinner-${id}`);
    const result  = document.getElementById(`result-${id}`);
    const notes   = document.getElementById(`notes-${id}`)?.value || '';

    btn.disabled        = true;
    spinner.style.display = 'block';
    result.style.display  = 'none';

    try {
      const res  = await fetch(`/api/proposals/${id}/apply-code`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({notes})
      });
      const data = await res.json();

      if (data.ok) {
        const files = (data.results || []).map(r =>
          `${r.file} <strong>${r.status}</strong>`
        ).join('<br>');
        result.innerHTML   = `Changes applied:<br>${files}`;
        result.className   = 'apply-result success';
        result.style.display = 'block';
        toast('Code applied');
        setTimeout(loadProposals, 1200);
      } else {
        result.textContent   = data.error || 'Failed to apply changes.';
        result.className     = 'apply-result error';
        result.style.display = 'block';
        btn.disabled         = false;
      }
    } catch(e) {
      result.textContent   = 'Request failed.';
      result.className     = 'apply-result error';
      result.style.display = 'block';
      btn.disabled         = false;
    } finally {
      spinner.style.display = 'none';
    }
  }

  async function markApplied(id) {
    const notes = document.getElementById(`notes-${id}`)?.value || '';
    await fetch(`/api/proposals/${id}/apply`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({notes})
    });
    toast('Marked as applied');
    loadProposals();
  }

  async function rejectProposal(id) {
    const notes = document.getElementById(`notes-${id}`)?.value || '';
    await fetch(`/api/proposals/${id}/reject`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({notes})
    });
    toast('Proposal rejected');
    loadProposals();
  }

  // -------------------------------------------------------------------------
  // Lessons
  // -------------------------------------------------------------------------
  async function loadLessons() {
    try {
      const res     = await fetch('/api/lessons');
      const lessons = await res.json();
      const el      = document.getElementById('lessons-list');
      if (!lessons.length) {
        el.innerHTML = '<div class="empty">No active lessons yet.</div>';
        return;
      }
      el.innerHTML = lessons.map(l => `
        <div class="lesson-card">
          <div class="lesson-dot"></div>
          <div>
            <div class="lesson-text">${esc(l.text)}</div>
            <div class="lesson-expiry">${l.expires_ts ? `Expires ${l.expires_ts.slice(0,10)}` : 'Never expires'}</div>
          </div>
        </div>`).join('');
    } catch(e) {
      document.getElementById('lessons-list').innerHTML =
        '<div class="empty">Could not load lessons.</div>';
    }
  }

  // -------------------------------------------------------------------------
  // Stats
  // -------------------------------------------------------------------------
  async function loadStats() {
    try {
      const res = await fetch('/api/stats?days=7');
      const s   = await res.json();
      const acc = s.accuracy_pct != null ? `${s.accuracy_pct}%` : '—';

      document.getElementById('stats-grid').innerHTML = `
        <div class="stat-card highlight">
          <div class="stat-value">${acc}</div>
          <div class="stat-label">Accuracy (7d)</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">${s.correct}</div>
          <div class="stat-label">Correct</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">${s.wrong}</div>
          <div class="stat-label">Wrong</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">${s.total - s.evaluated}</div>
          <div class="stat-label">Pending</div>
        </div>`;

      const tbody  = document.getElementById('breakdown-body');
      const byType = s.by_decision_type || {};
      if (!Object.keys(byType).length) {
        tbody.innerHTML = '<tr><td colspan="4" style="color:#94a3b8;padding:16px 0">No data yet.</td></tr>';
        return;
      }
      tbody.innerHTML = Object.entries(byType).map(([type, counts]) => {
        const c   = counts.correct || 0;
        const w   = counts.wrong   || 0;
        const n   = c + w;
        const pct = n ? Math.round(c / n * 100) : 0;
        return `<tr>
          <td>${esc(type)}</td><td>${c}</td><td>${w}</td>
          <td>${pct}%<div class="accuracy-bar"><div class="accuracy-fill" style="width:${pct}%"></div></div></td>
        </tr>`;
      }).join('');
    } catch(e) {
      document.getElementById('stats-grid').innerHTML =
        '<div class="empty">Could not load stats.</div>';
    }
  }

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------
  function esc(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  let toastTimer;
  function toast(msg) {
    const el = document.getElementById('toast');
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 2500);
  }

  async function boot() {
    try {
      const res  = await fetch('/api/info');
      const info = await res.json();
      document.getElementById('system-name').textContent = info.name;
      document.title = `Engram · ${info.name}`;
      if (info.accuracy_pct != null) {
        document.getElementById('header-stats').textContent =
          `${info.accuracy_pct}% accuracy · ${info.pending_proposals} pending`;
      }
    } catch(_) {}
    loadProposals();
  }

  boot();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

def create_app(schema=None) -> Flask:
    app = Flask(__name__)
    app.config["SCHEMA"] = schema

    @app.route("/")
    def index():
        return _HTML

    @app.route("/api/info")
    def info():
        stats   = stats_mod.compute(window_hours=168)
        pending = db.fetchone(
            "SELECT COUNT(*) as n FROM proposals WHERE status = 'pending'"
        ) or {}
        name = schema.name if schema else "Engram"
        return jsonify({
            "name":              name,
            "accuracy_pct":      stats["accuracy_pct"],
            "pending_proposals": pending.get("n", 0),
        })

    @app.route("/api/proposals")
    def get_proposals():
        show_all = request.args.get("all") == "1"
        if show_all:
            rows = db.fetchall(
                "SELECT * FROM proposals ORDER BY written_ts DESC LIMIT 200"
            )
        else:
            rows = db.fetchall(
                "SELECT * FROM proposals WHERE status = 'pending' "
                "ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
                "written_ts DESC LIMIT 100"
            )
        for r in rows:
            try:
                r["evidence"] = json.loads(r.get("evidence") or "[]")
            except Exception:
                r["evidence"] = []
        return jsonify(rows)

    @app.route("/api/proposals/<int:proposal_id>/apply", methods=["POST"])
    def apply_proposal(proposal_id: int):
        data  = request.get_json() or {}
        notes = data.get("notes", "")
        now   = datetime.now(timezone.utc).isoformat()
        db.execute(
            "UPDATE proposals SET status = 'applied', implemented_ts = ?, user_notes = ? WHERE id = ?",
            (now, notes, proposal_id),
        )
        return jsonify({"ok": True})

    @app.route("/api/proposals/<int:proposal_id>/apply-code", methods=["POST"])
    def apply_code(proposal_id: int):
        """
        Read the affected files, pass each one + the proposed code change to Claude,
        let Claude apply the change intelligently, write the result back.
        """
        proposal = db.fetchone("SELECT * FROM proposals WHERE id = ?", (proposal_id,))
        if not proposal:
            return jsonify({"error": "Proposal not found"}), 404

        s = app.config.get("SCHEMA")
        if not s or not s.codebase.dir:
            return jsonify({"error": "codebase.dir not configured in engram.yaml"}), 400

        affected = [
            f.strip()
            for f in (proposal.get("affected_files") or "").split(",")
            if f.strip()
        ]
        if not affected:
            return jsonify({"error": "No affected_files on this proposal"}), 400

        # Use explicit code_change if available, otherwise use the proposal description
        instruction = (proposal.get("code_change") or "").strip() or (
            f"Problem: {proposal.get('problem', '')}\n\n"
            f"Proposal: {proposal.get('proposal', '')}"
        )

        root    = Path(s.codebase.dir).resolve()
        results = []

        import anthropic
        client = anthropic.Anthropic()

        for rel_path in affected:
            full_path = root / rel_path
            if not full_path.exists():
                results.append({"file": rel_path, "status": "not found"})
                continue

            current = full_path.read_text(encoding="utf-8", errors="ignore")

            try:
                resp = client.messages.create(
                    model       = s.llm.model,
                    max_tokens  = 8000,
                    temperature = 0,
                    system      = (
                        "You are a precise code editor. "
                        "Apply the requested change to the file exactly as intended. "
                        "Return ONLY the complete updated file content — "
                        "no explanation, no markdown fences, no commentary."
                    ),
                    messages=[{
                        "role": "user",
                        "content": (
                            f"## Requested change\n{instruction}\n\n"
                            f"## Current content of {rel_path}\n{current}\n\n"
                            f"Apply the requested change to this file. "
                            f"Return the complete updated file content only."
                        ),
                    }],
                )

                updated = resp.content[0].text.strip()

                # Strip accidental markdown fences
                if updated.startswith("```"):
                    lines   = updated.splitlines()
                    updated = "\n".join(
                        lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
                    ).strip()

                full_path.write_text(updated, encoding="utf-8")
                results.append({"file": rel_path, "status": "applied"})
                logger.info("apply-code: wrote %s", rel_path)

            except Exception as e:
                logger.error("apply-code: failed for %s — %s", rel_path, e)
                results.append({"file": rel_path, "status": f"error: {e}"})

        # Mark applied if at least one file succeeded
        if any(r["status"] == "applied" for r in results):
            data  = request.get_json() or {}
            notes = data.get("notes", "")
            now   = datetime.now(timezone.utc).isoformat()
            db.execute(
                "UPDATE proposals SET status = 'applied', implemented_ts = ?, user_notes = ? WHERE id = ?",
                (now, notes, proposal_id),
            )

        return jsonify({"ok": True, "results": results})

    @app.route("/api/proposals/<int:proposal_id>/reject", methods=["POST"])
    def reject_proposal(proposal_id: int):
        data  = request.get_json() or {}
        notes = data.get("notes", "")
        db.execute(
            "UPDATE proposals SET status = 'rejected', user_notes = ? WHERE id = ?",
            (notes, proposal_id),
        )
        return jsonify({"ok": True})

    @app.route("/api/lessons")
    def get_lessons():
        now  = datetime.now(timezone.utc).isoformat()
        rows = db.fetchall(
            "SELECT id, text, written_ts, expires_ts FROM lessons "
            "WHERE type = 'lesson' AND (expires_ts IS NULL OR expires_ts > ?) "
            "ORDER BY written_ts DESC",
            (now,),
        )
        return jsonify(rows)

    @app.route("/api/stats")
    def get_stats():
        days  = int(request.args.get("days", 7))
        stats = stats_mod.compute(window_hours=days * 24)
        return jsonify(stats)

    return app
