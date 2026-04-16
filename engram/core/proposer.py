"""Nightly proposal generator — analyses today's decisions, proposes improvements."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone, timedelta

import anthropic

import engram.core.db as db
import engram.core.stats as stats_mod
import engram.core.codebase as codebase_mod
from engram.schema import EngramSchema

logger  = logging.getLogger(__name__)
_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def run(schema: EngramSchema) -> None:
    """Load today's data, call LLM x2 (engineering + creative), write proposals."""
    logger.info("Proposer: starting for '%s'", schema.name)
    try:
        data = _collect(schema)

        proposals = _call_llm(data, schema)
        n = _write(proposals, data["today_str"])
        logger.info("Proposer: %d engineering proposals written", n)

        creative = _call_llm_creative(data, schema)
        nc = _write(creative, data["today_str"])
        logger.info("Proposer: %d creative proposals written", nc)
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

    # Structured stats — computed before the LLM call, not derived from text
    daily_stats  = stats_mod.compute(window_hours=24)
    weekly_stats = stats_mod.compute(window_hours=168)
    stats_text   = (
        f"Last 24h:\n{stats_mod.format_for_llm(daily_stats)}\n\n"
        f"Last 7 days:\n{stats_mod.format_for_llm(weekly_stats)}"
    )

    # Parameter schema
    params_lines = [f"System: {schema.name}", f"Domain: {schema.domain[:300]}"]
    if schema.parameters:
        params_lines.append("Tunable parameters:")
        for p in schema.parameters:
            params_lines.append(
                f"  {p.get('name','')} ({p.get('type','?')}): {p.get('description','')}"
            )

    # Codebase context — include files from config + files recently flagged in proposals
    extra_files    = codebase_mod.recent_affected_files()
    codebase_block = codebase_mod.read_context(schema.codebase, extra_files=extra_files)

    return {
        "today_str":  today_str,
        "diary":      diary_text,
        "decisions":  "\n".join(decision_lines) or "(no decisions today)",
        "lessons":    "\n".join(lesson_lines)   or "(no active lessons)",
        "params":     "\n".join(params_lines),
        "stats":      stats_text,
        "categories": " | ".join(schema.proposal_categories),
        "codebase":   codebase_block,
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
- The proposal field explains direction + reasoning.
- If source files are provided, the code_change field MUST contain a concrete, \
minimal code snippet or diff showing exactly what to change. Be specific — show the \
before/after or the exact lines to add/modify. If no source files are provided, \
omit code_change entirely.
- If today was quiet with no notable errors, return fewer (1-2) proposals.
- Focus on systematic patterns, not one-off noise.
- Cost proposals are valuable: flag unnecessary LLM calls, redundant cycles, \
sources that never produce useful output."""

_SYSTEM_CREATIVE_TMPL = """\
You are a creative strategist and domain expert reviewing {name}.

Domain: {domain}

Your job: look at today's data and invent genuinely NEW capabilities, signals, sources, \
or strategies that do NOT yet exist in the system. Do not fix existing bugs — that is \
handled elsewhere. Think boldly.

Rules:
- Propose things that are ABSENT from the current system entirely.
- Each idea must be grounded in the data (accuracy patterns, missed signals, \
blind spots visible in the decisions).
- Do not suggest code tweaks. Suggest what the system SHOULD be able to do or monitor.
- No affected_files. No code_change. These are strategic recommendations.
- If the data is too thin to inspire genuine new ideas, return 1-2 ideas max."""

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
{codebase_section}
---
Analyse {name}'s behaviour today. Propose 3-7 improvements.

Categories: {categories}

Each proposal MUST:
1. Cite specific evidence from today (IDs, values, timestamps).
2. Explain WHY this behaviour occurred — what rule or gap caused it.
3. Suggest direction + reasoning (not code or diffs).
{file_instruction}
Return ONLY a JSON array — no prose, no markdown fences:
[{{
  "category": "<one from the categories list>",
  "priority": "high|medium|low",
  "title": "<max 80 chars>",
  "problem": "<what went wrong or what opportunity was missed>",
  "evidence": ["<specific example 1>", "<specific example 2>"],
  "proposal": "<direction and reasoning — what to change and why>",
  "affected_files": "<comma-separated file paths relative to codebase root>",
  "code_change": "<concrete snippet or diff — only when source files were provided, otherwise omit>"
}}]"""

_PROMPT_CREATIVE_TMPL = """\
## Today's Diary
{diary}

## Decisions (last 24h) with outcomes
{decisions}

## Active lessons
{lessons}

## Stats today
{stats}

## System parameters
{params}
---
Based on today's data for {name}, propose 2-4 genuinely NEW ideas — \
capabilities, signals, data sources, or strategies that do not exist in the system yet.

Each idea must be grounded in a pattern or gap visible in the data above.

Categories: {categories}

Return ONLY a JSON array — no prose, no markdown fences:
[{{
  "category": "<one from the categories list>",
  "priority": "high|medium|low",
  "title": "<max 80 chars>",
  "problem": "<what gap or opportunity this addresses>",
  "evidence": ["<pattern or signal from today that motivates this>"],
  "proposal": "<the new idea — what it is, why it would help, how it would work at a high level>"
}}]"""


def _parse_json_array(raw: str) -> list:
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()
    start = raw.find("[")
    end   = raw.rfind("]")
    if start == -1 or end == -1:
        return []
    return json.loads(raw[start:end + 1])


def _call_llm(data: dict, schema: EngramSchema) -> list:
    system = _SYSTEM_TMPL.format(
        name   = schema.name,
        domain = schema.domain[:300] or f"a decision system called {schema.name}",
    )
    has_codebase   = bool(data.get("codebase"))
    codebase_section = (
        f"\n## Source files\n{data['codebase']}\n"
        if has_codebase else ""
    )
    file_instruction = (
        "4. Reference specific file paths and line areas from the source files above when relevant.\n"
        if has_codebase else ""
    )

    prompt = _PROMPT_TMPL.format(
        name             = schema.name,
        diary            = data["diary"],
        decisions        = data["decisions"],
        lessons          = data["lessons"],
        params           = data["params"],
        stats            = data["stats"],
        categories       = data["categories"],
        codebase_section = codebase_section,
        file_instruction = file_instruction,
    )
    resp = _get_client().messages.create(
        model       = schema.llm.model,
        max_tokens  = schema.llm.max_tokens,
        temperature = 0,
        system      = system,
        messages    = [{"role": "user", "content": prompt}],
    )
    return _parse_json_array(resp.content[0].text.strip())


def _call_llm_creative(data: dict, schema: EngramSchema) -> list:
    """Second LLM pass: creative strategist inventing new capabilities."""
    creative_categories = (
        schema.creative_proposal_categories
        if schema.creative_proposal_categories
        else ["new_signal", "new_source", "new_strategy", "opportunity"]
    )
    system = _SYSTEM_CREATIVE_TMPL.format(
        name   = schema.name,
        domain = schema.domain[:300] or f"a decision system called {schema.name}",
    )
    prompt = _PROMPT_CREATIVE_TMPL.format(
        name       = schema.name,
        diary      = data["diary"],
        decisions  = data["decisions"],
        lessons    = data["lessons"],
        params     = data["params"],
        stats      = data["stats"],
        categories = " | ".join(creative_categories),
    )
    resp = _get_client().messages.create(
        model       = schema.llm.model,
        max_tokens  = schema.llm.max_tokens,
        temperature = 0.7,
        system      = system,
        messages    = [{"role": "user", "content": prompt}],
    )
    proposals = _parse_json_array(resp.content[0].text.strip())
    # Ensure no affected_files / code_change leaks in from the model
    for p in proposals:
        p.pop("affected_files", None)
        p.pop("code_change", None)
    return proposals


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

        code_change = (p.get("code_change") or "").strip() or None
        db.execute(
            "INSERT INTO proposals "
            "(written_ts, analysis_date, category, priority, title, problem, "
            "evidence, proposal, affected_files, code_change, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
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
                code_change,
            ),
        )
        written += 1
        has_code = " [+code]" if code_change else ""
        logger.info("Proposer: [%s]%s %s", p.get("category", "?"), has_code, title[:60])

    return written
