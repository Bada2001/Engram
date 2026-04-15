"""Daily diary — summarises decisions + outcomes for the day."""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta

import engram.core.db as db
import engram.core.outcomes as outcomes_mod
import engram.core.stats as stats_mod
from engram.schema import EngramSchema

logger = logging.getLogger(__name__)

_ERROR_RATE_THRESHOLD = 0.30  # trigger early extraction if >30% wrong
_MIN_EVALUATED        = 5     # minimum evaluated decisions before checking threshold


def write(schema: EngramSchema) -> None:
    """
    Read today's decisions, assess outcomes, write a diary entry to lessons table.
    Triggers lesson extraction immediately if error rate exceeds threshold.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rows  = db.fetchall(
        "SELECT * FROM decisions WHERE ts >= ? ORDER BY ts ASC",
        (since,),
    )
    if not rows:
        logger.info("Diary: no decisions in last 24h, skipping")
        return

    correct_count = wrong_count = 0
    lines = []

    for row in rows:
        dec     = row.get("decision", "?")
        outcome = outcomes_mod.assess(row, schema.outcome)

        if outcome and not row.get("outcome"):
            db.execute(
                "UPDATE decisions SET outcome = ?, outcome_ts = ? WHERE decision_id = ?",
                (outcome, datetime.now(timezone.utc).isoformat(), row["decision_id"]),
            )

        tag = ""
        if outcome == "correct":
            tag = " (correct)"
            correct_count += 1
        elif outcome == "wrong":
            tag = " (wrong)"
            wrong_count += 1
        elif outcome == "inconclusive":
            tag = " (inconclusive)"

        ts_short = (row.get("ts") or "")[:16]
        lines.append(f"[{ts_short}] {row.get('decision_id','?')} → {dec}{tag}")

    total    = correct_count + wrong_count
    accuracy = f"{correct_count}/{total} correct" if total else "no outcomes yet"

    # Compute structured stats and embed them — LLM gets facts, not just raw text
    daily_stats = stats_mod.compute(window_hours=24)
    stats_block = stats_mod.format_for_llm(daily_stats)

    text  = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d')}] {schema.name} — {accuracy}\n"
    text += f"\n{stats_block}\n\n"
    text += "\n".join(lines)

    expires           = (datetime.now(timezone.utc) + timedelta(days=8)).isoformat()
    baseline_accuracy = daily_stats["accuracy_pct"]

    db.execute(
        "INSERT INTO lessons (written_ts, expires_ts, type, text, baseline_accuracy) "
        "VALUES (?, ?, 'diary', ?, ?)",
        (datetime.now(timezone.utc).isoformat(), expires, text, baseline_accuracy),
    )
    logger.info("Diary: written (%d decisions, %s)", len(rows), accuracy)

    # Volume-aware error threshold — percentage-based, not a raw count
    error_rate = stats_mod.error_rate(window_hours=24)
    if error_rate is not None and error_rate >= _ERROR_RATE_THRESHOLD:
        logger.info(
            "Diary: error rate %.0f%% exceeds threshold — triggering lesson extraction",
            error_rate * 100,
        )
        from engram.core import extractor
        extractor.extract(schema)
