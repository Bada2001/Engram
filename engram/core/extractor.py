"""Lesson extraction and weekly checkpoint via LLM."""
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


def extract(schema: EngramSchema) -> None:
    """
    Read recent diary entries, extract 3-5 short actionable lessons via LLM.
    Lessons are stored with a TTL and returned by eng.active_lessons() for
    injection into the host system's prompts.
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

    prompt = f"""\
You are reviewing a decision system called "{schema.name}".

Domain context: {domain_blurb}

Extract 3-5 SHORT, SPECIFIC, ACTIONABLE lessons from the decision diary below.

Each lesson must be:
- One sentence, max 400 characters
- Grounded in a specific pattern from the diary (not generic advice)
- Actionable — it should change a specific future decision

For each lesson also set ttl_days — how long it should remain active:
- Context-specific (e.g. "source X has been noisy for 3 weeks"): 14-30 days
- Behavioral rules tied to current regime: 60-90 days
- Universal decision rules: 365 days
- Structural / permanent rules: null (never expires)

DIARY:
{diary_text}

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

        lessons = json.loads(raw[start:end + 1])
        now     = datetime.now(timezone.utc)
        written = 0

        for lesson in lessons:
            if not isinstance(lesson, dict) or len(lesson.get("text", "")) < 10:
                continue
            ttl     = lesson.get("ttl_days")
            expires = (now + timedelta(days=int(ttl))).isoformat() if ttl else None
            db.execute(
                "INSERT INTO lessons (written_ts, expires_ts, type, text) VALUES (?, ?, 'lesson', ?)",
                (now.isoformat(), expires, lesson["text"]),
            )
            written += 1
            logger.info("Extractor: lesson added (ttl=%s) — %s", ttl, lesson["text"][:80])

        logger.info("Extractor: %d lessons written", written)

    except Exception as e:
        logger.error("Extractor: failed — %s", e)


def weekly_checkpoint(schema: EngramSchema) -> None:
    """
    Compress the week's diary entries into one dense paragraph.
    Replaces individual diary rows with a single checkpoint entry that lives for 60 days.
    """
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    rows = db.fetchall(
        "SELECT id, text FROM lessons WHERE type = 'diary' AND written_ts >= ? ORDER BY written_ts ASC",
        (week_ago,),
    )
    if len(rows) < 3:
        logger.info("Checkpoint: fewer than 3 diary entries, skipping")
        return

    diary_text = "\n\n".join(r["text"] for r in rows)
    prompt = f"""\
Compress these daily diary entries for "{schema.name}" into a single dense paragraph.
Include: overall accuracy, dominant patterns, key events that drove outcomes, any systematic errors.
Max 200 words. Factual, no opinions.

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

        # Replace diary entries with the checkpoint
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
