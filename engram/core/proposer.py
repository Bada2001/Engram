"""Nightly proposal generator — analyses today's decisions, proposes improvements."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone, timedelta

import anthropic

import engram.core.db as db
from engram.schema import EngramSchema

logger  = logging.getLogger(__name__)
_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def run(schema: EngramSchema) -> None:
    """Load today's data, call LLM, write proposals. Idempotent (deduped by title)."""
    logger.info("Proposer: starting for '%s'", schema.name)
    try:
        data      = _collect(schema)
        proposals = _call_llm(data, schema)
        n         = _write(proposals, data["today_str"])
        logger.info("Proposer: %d proposals written", n)
    except Exception as e:
        logger.error("Proposer: failed — %s", e)
        import traceback
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def _collect(schema: EngramSchema) -> dict:
    since     = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Decisions + outcomes
    decisions = db.fetchall(
        "SELECT ts, decision_id, decision, context, outcome FROM decisions "
        "WHERE ts >= ? ORDER BY ts ASC",
        (since,),
    )
    decision_lines = []
    for d in decisions:
        ctx = {}
        try:
            ctx = json.loads(d.get("context") or "{}")
        except Exception:
            pass
        outcome_str = d.get("outcome") or "pending"
        ctx_short   = json.dumps(ctx)[:120].replace("\n", " ")
        decision_lines.append(
            f"[{d['ts'][:16]}] {d['decision_id']} → {d['decision']} "
            f"outcome={outcome_str} ctx={ctx_short}"
        )

    # Latest diary / checkpoint
    diary_rows = db.fetchall(
        "SELECT text FROM lessons "
        "WHERE type IN ('diary','checkpoint') AND written_ts >= ? "
        "ORDER BY written_ts DESC LIMIT 1",
        ((datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(),),
    )
    diary_text = diary_rows[0]["text"] if diary_rows else "(no diary yet)"

    # Active lessons
    now_iso     = datetime.now(timezone.utc).isoformat()
    lesson_rows = db.fetchall(
        "SELECT text, expires_ts FROM lessons "
        "WHERE type = 'lesson' AND (expires_ts IS NULL OR expires_ts > ?) "
        "ORDER BY written_ts DESC LIMIT 20",
        (now_iso,),
    )
    lesson_lines = []
    for l in lesson_rows:
        exp = (l.get("expires_ts") or "")[:10] or "never"
        lesson_lines.append(f"• [{exp}] {l['text']}")

    # Stats
    total   = len(decisions)
    correct = sum(1 for d in decisions if d.get("outcome") == "correct")
    wrong   = sum(1 for d in decisions if d.get("outcome") == "wrong")
    stats_text = (
        f"Decisions: {total} | Correct: {correct} | Wrong: {wrong} "
        f"| Pending: {total - correct - wrong}"
    )

    # Parameter schema
    params_lines = [f"System: {schema.name}", f"Domain: {schema.domain[:300]}"]
    if schema.parameters:
        params_lines.append("Tunable parameters:")
        for p in schema.parameters:
            params_lines.append(
                f"  {p.get('name','')} ({p.get('type','?')}): {p.get('description','')}"
            )

    return {
        "today_str": today_str,
        "diary":     diary_text,
        "decisions": "\n".join(decision_lines) or "(no decisions today)",
        "lessons":   "\n".join(lesson_lines)   or "(no active lessons)",
        "params":    "\n".join(params_lines),
        "stats":     stats_text,
        "categories": " | ".join(schema.proposal_categories),
    }


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

_SYSTEM_TMPL = """\
You are a senior engineer reviewing {name}.

Domain: {domain}

Your job: analyse today's system behaviour and propose specific, evidence-backed improvements.

Rules:
- Every proposal MUST cite specific evidence from today (IDs, values, timestamps).
- The proposal field explains direction + reasoning — not code, not diffs.
- If today was quiet with no notable errors, return fewer (1-2) proposals.
- Focus on systematic patterns, not one-off noise.
- Cost proposals are valuable: flag unnecessary LLM calls, redundant cycles, \
sources that never produce useful output."""

_PROMPT_TMPL = """\
## Today's Diary
{diary}

## Decisions (last 24h) with outcomes
{decisions}

## Active lessons already applied
{lessons}

## System parameters
{params}

## Stats today
{stats}

---
Analyse {name}'s behaviour today. Propose 3-7 improvements.

Categories: {categories}

Each proposal MUST:
1. Cite specific evidence from today (IDs, values, timestamps).
2. Explain WHY this behaviour occurred — what rule or gap caused it.
3. Suggest direction + reasoning (not code or diffs).

Return ONLY a JSON array — no prose, no markdown fences:
[{{
  "category": "<one from the categories list>",
  "priority": "high|medium|low",
  "title": "<max 80 chars>",
  "problem": "<what went wrong or what opportunity was missed>",
  "evidence": ["<specific example 1>", "<specific example 2>"],
  "proposal": "<direction and reasoning — what to change and why>",
  "affected_files": "<comma-separated file paths>"
}}]"""


def _call_llm(data: dict, schema: EngramSchema) -> list:
    system = _SYSTEM_TMPL.format(
        name   = schema.name,
        domain = schema.domain[:300] or f"a decision system called {schema.name}",
    )
    prompt = _PROMPT_TMPL.format(
        name       = schema.name,
        diary      = data["diary"],
        decisions  = data["decisions"],
        lessons    = data["lessons"],
        params     = data["params"],
        stats      = data["stats"],
        categories = data["categories"],
    )
    resp = _get_client().messages.create(
        model       = schema.llm.model,
        max_tokens  = schema.llm.max_tokens,
        temperature = 0,
        system      = system,
        messages    = [{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()

    # Strip markdown fences if model adds them anyway
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()

    start = raw.find("[")
    end   = raw.rfind("]")
    if start == -1 or end == -1:
        logger.error("Proposer: no JSON array in LLM response")
        return []
    return json.loads(raw[start:end + 1])


# ---------------------------------------------------------------------------
# Write with dedup
# ---------------------------------------------------------------------------

def _write(proposals: list, today_str: str) -> int:
    written = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for p in proposals:
        if not isinstance(p, dict):
            continue
        title = (p.get("title") or "").strip()
        if len(title) < 5:
            continue

        # Dedup: skip if a pending proposal with this exact title already exists
        existing = db.fetchone(
            "SELECT id FROM proposals WHERE title = ? AND status = 'pending'",
            (title,),
        )
        if existing:
            logger.info("Proposer: duplicate skipped — %s", title[:60])
            continue

        db.execute(
            "INSERT INTO proposals "
            "(written_ts, analysis_date, category, priority, title, problem, "
            "evidence, proposal, affected_files, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
            (
                now_iso,
                today_str,
                (p.get("category") or "parameter").lower(),
                (p.get("priority") or "medium").lower(),
                title,
                (p.get("problem") or "").strip(),
                json.dumps(p.get("evidence") or []),
                (p.get("proposal") or "").strip(),
                (p.get("affected_files") or "").strip(),
            ),
        )
        written += 1
        logger.info("Proposer: [%s] %s", p.get("category", "?"), title[:60])

    return written
