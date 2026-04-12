"""Daily diary — summarises decisions + outcomes for the day."""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta

import engram.core.db as db
import engram.core.outcomes as outcomes_mod
from engram.schema import EngramSchema

logger = logging.getLogger(__name__)


def write(schema: EngramSchema) -> None:
    """
    Read today's decisions, assess outcomes, write a diary entry to lessons table.
    If wrong_count >= 3, triggers lesson extraction immediately.
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

        # Persist outcome if newly assessed
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

    text  = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d')}] {schema.name} — {accuracy}\n"
    text += "\n".join(lines)

    expires = (datetime.now(timezone.utc) + timedelta(days=8)).isoformat()
    db.execute(
        "INSERT INTO lessons (written_ts, expires_ts, type, text) VALUES (?, ?, 'diary', ?)",
        (datetime.now(timezone.utc).isoformat(), expires, text),
    )
    logger.info("Diary: written (%d decisions, %s)", len(rows), accuracy)

    if wrong_count >= 3:
        logger.info("Diary: %d wrong calls — triggering lesson extraction", wrong_count)
        from engram.core import extractor
        extractor.extract(schema)
