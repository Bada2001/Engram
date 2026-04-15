"""Lesson extraction, validation, and weekly checkpoint via LLM."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone, timedelta

import anthropic

import engram.core.db as db
import engram.core.stats as stats_mod
from engram.schema import EngramSchema

logger  = logging.getLogger(__name__)
_client = None

_LESSON_IMPROVEMENT_THRESHOLD = 0.05   # 5pp improvement needed to validate a lesson
_LESSON_DEGRADATION_THRESHOLD = -0.08  # 8pp drop triggers early expiry


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def extract(schema: EngramSchema) -> None:
    """
    Read recent diary entries + structured stats, extract actionable lessons via LLM.
    Skips lessons that duplicate existing active ones.
    Lessons are stored with a TTL and returned by eng.active_lessons().
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    rows   = db.fetchall(
        "SELECT text FROM lessons "
        "WHERE type IN ('diary','checkpoint') AND written_ts >= ? "
        "ORDER BY written_ts DESC LIMIT 14",
        (cutoff,),
    )
    if not rows:
        logger.info("Extractor: no diary entries, skipping")
        return

    diary_text = "\n\n".join(r["text"] for r in rows)
    domain_blurb = schema.domain[:400] if schema.domain else f"a decision system called {schema.name}"

    # Structured stats over the same window — LLM gets facts, not just prose
    weekly_stats = stats_mod.compute(window_hours=14 * 24)
    stats_block  = stats_mod.format_for_llm(weekly_stats)

    # Existing active lessons — LLM avoids duplicating them
    now_iso      = datetime.now(timezone.utc).isoformat()
    active_rows  = db.fetchall(
        "SELECT text FROM lessons "
        "WHERE type = 'lesson' AND (expires_ts IS NULL OR expires_ts > ?) "
        "ORDER BY written_ts DESC LIMIT 20",
        (now_iso,),
    )
    existing_lessons = "\n".join(f"- {r['text']}" for r in active_rows) or "(none)"

    prompt = f"""\
You are reviewing a decision system called "{schema.name}".

Domain context: {domain_blurb}

## Accuracy statistics (last 14 days)
{stats_block}

## Decision diary (last 14 days)
{diary_text}

## Already active lessons (do NOT repeat these)
{existing_lessons}

Extract 3-5 SHORT, SPECIFIC, ACTIONABLE lessons from the data above.

Each lesson must:
- Be one sentence, max 400 characters
- Be grounded in a specific pattern visible in the statistics or diary (not generic advice)
- Be actionable — it must change a specific future decision
- Not duplicate or restate an already active lesson

For each lesson set ttl_days — how long it should remain active:
- Pattern tied to a short-term condition: 14–30 days
- Behavioural rule for the current operating regime: 60–90 days
- Universal rule that always applies: 365 days
- Permanent structural rule: null (never expires)

Return ONLY a JSON array — no prose, no fences:
[{{"text": "lesson text", "ttl_days": N_or_null}}]"""

    try:
        resp = _get_client().messages.create(
            model       = schema.llm.model,
            max_tokens  = 600,
            temperature = 0,
            system      = f"Extract patterns from a {schema.name} decision diary. Be specific and brief.",
            messages    = [{"role": "user", "content": prompt}],
        )
        raw   = resp.content[0].text.strip()
        start = raw.find("[")
        end   = raw.rfind("]")
        if start == -1 or end == -1:
            logger.error("Extractor: no JSON array in response")
            return

        lessons  = json.loads(raw[start:end + 1])
        now      = datetime.now(timezone.utc)
        baseline = weekly_stats["accuracy_pct"]
        written  = 0

        for lesson in lessons:
            if not isinstance(lesson, dict) or len(lesson.get("text", "")) < 10:
                continue
            ttl     = lesson.get("ttl_days")
            expires = (now + timedelta(days=int(ttl))).isoformat() if ttl else None
            db.execute(
                "INSERT INTO lessons "
                "(written_ts, expires_ts, type, text, source_data, baseline_accuracy) "
                "VALUES (?, ?, 'lesson', ?, ?, ?)",
                (now.isoformat(), expires, lesson["text"], diary_text, baseline),
            )
            written += 1
            logger.info("Extractor: lesson added (ttl=%s) — %s", ttl, lesson["text"][:80])

        logger.info("Extractor: %d lessons written", written)

    except Exception as e:
        logger.error("Extractor: failed — %s", e)


def validate_lessons() -> None:
    """
    Check whether active lessons are actually improving outcomes.
    - If accuracy has improved >= 5pp since a lesson was added: keep it, log validation
    - If accuracy has dropped >= 8pp since a lesson was added: expire it early
    Runs weekly alongside lesson extraction.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    lessons = db.fetchall(
        "SELECT id, text, written_ts, baseline_accuracy FROM lessons "
        "WHERE type = 'lesson' "
        "AND baseline_accuracy IS NOT NULL "
        "AND (expires_ts IS NULL OR expires_ts > ?) "
        "ORDER BY written_ts ASC",
        (now_iso,),
    )

    if not lessons:
        logger.info("Validator: no lessons with baselines to validate")
        return

    # Current accuracy over the last 7 days
    current_stats   = stats_mod.compute(window_hours=168)
    current_accuracy = current_stats["accuracy_pct"]

    if current_accuracy is None:
        logger.info("Validator: insufficient data for validation")
        return

    expired = validated = 0

    for lesson in lessons:
        baseline = lesson["baseline_accuracy"]
        delta    = (current_accuracy - baseline) / 100  # convert pct to fraction

        if delta <= _LESSON_DEGRADATION_THRESHOLD:
            # Accuracy has dropped since this lesson was added — expire it
            db.execute(
                "UPDATE lessons SET expires_ts = ? WHERE id = ?",
                (now_iso, lesson["id"]),
            )
            expired += 1
            logger.warning(
                "Validator: lesson expired (accuracy dropped %.1fpp) — %s",
                delta * 100, lesson["text"][:80],
            )
        elif delta >= _LESSON_IMPROVEMENT_THRESHOLD:
            validated += 1
            logger.info(
                "Validator: lesson validated (accuracy up %.1fpp) — %s",
                delta * 100, lesson["text"][:80],
            )

    logger.info(
        "Validator: %d lessons validated, %d expired (current accuracy %.1f%%)",
        validated, expired, current_accuracy,
    )


def weekly_checkpoint(schema: EngramSchema) -> None:
    """
    Compress the week's diary entries into one dense paragraph.
    Replaces individual diary rows with a single checkpoint entry that lives for 60 days.
    Also runs lesson validation.
    """
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    rows = db.fetchall(
        "SELECT id, text FROM lessons WHERE type = 'diary' AND written_ts >= ? ORDER BY written_ts ASC",
        (week_ago,),
    )
    if len(rows) < 3:
        logger.info("Checkpoint: fewer than 3 diary entries, skipping")
    else:
        diary_text   = "\n\n".join(r["text"] for r in rows)
        weekly_stats = stats_mod.compute(window_hours=168)
        stats_block  = stats_mod.format_for_llm(weekly_stats)

        prompt = f"""\
Compress these daily diary entries for "{schema.name}" into a single dense paragraph.
Include: overall accuracy, dominant patterns, key events that drove outcomes, any systematic errors.
Max 200 words. Factual, no opinions.

## Accuracy statistics this week
{stats_block}

## Daily entries
{diary_text}"""

        try:
            resp = _get_client().messages.create(
                model       = schema.llm.model,
                max_tokens  = 300,
                temperature = 0,
                system      = "Compress decision diary entries. Factual and dense.",
                messages    = [{"role": "user", "content": prompt}],
            )
            checkpoint_text = resp.content[0].text.strip()

            ids = ",".join("?" * len(rows))
            db.execute(
                f"DELETE FROM lessons WHERE id IN ({ids})",
                tuple(r["id"] for r in rows),
            )
            expires = (datetime.now(timezone.utc) + timedelta(days=60)).isoformat()
            db.execute(
                "INSERT INTO lessons (written_ts, expires_ts, type, text) VALUES (?, ?, 'checkpoint', ?)",
                (datetime.now(timezone.utc).isoformat(), expires, checkpoint_text),
            )
            logger.info("Checkpoint: written, compressed %d diary entries", len(rows))

        except Exception as e:
            logger.error("Checkpoint: failed — %s", e)

    # Always run validation on checkpoint day
    validate_lessons()
