"""Statistical summaries computed from decisions — used before every LLM call."""
from __future__ import annotations
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta
import json
import logging

import engram.core.db as db

logger = logging.getLogger(__name__)


def compute(window_hours: int = 24) -> dict:
    """
    Compute accuracy statistics over the given window.
    Returns a structured dict. Use format_for_llm() to turn it into prompt text.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    rows  = db.fetchall(
        "SELECT decision, context, outcome FROM decisions WHERE ts >= ? ORDER BY ts ASC",
        (since,),
    )

    evaluated    = [r for r in rows if r.get("outcome") in ("correct", "wrong")]
    correct      = sum(1 for r in evaluated if r["outcome"] == "correct")
    wrong        = len(evaluated) - correct
    inconclusive = sum(1 for r in rows if r.get("outcome") == "inconclusive")

    # Accuracy by decision type
    by_type: dict[str, Counter] = defaultdict(Counter)
    for r in evaluated:
        by_type[r["decision"]][r["outcome"]] += 1

    # Accuracy by categorical context fields (string values only, reasonable cardinality)
    by_context: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for r in evaluated:
        try:
            ctx = json.loads(r.get("context") or "{}")
            for k, v in ctx.items():
                if isinstance(v, str) and len(v) < 60:
                    by_context[k][v][r["outcome"]] += 1
        except Exception:
            pass

    n_eval = len(evaluated)
    n_total = len(rows)
    return {
        "window_hours":       window_hours,
        "total":              n_total,
        "evaluated":          n_eval,
        "correct":            correct,
        "wrong":              wrong,
        "inconclusive":       inconclusive,
        "accuracy_pct":       round(correct / n_eval * 100, 1) if n_eval else None,
        "inconclusive_rate":  round(inconclusive / (n_eval + inconclusive), 4) if (n_eval + inconclusive) > 0 else None,
        "by_decision_type":   {k: dict(v) for k, v in by_type.items()},
        "by_context_field":   {
            k: {v2: dict(c) for v2, c in vals.items()}
            for k, vals in by_context.items()
        },
    }


def format_for_llm(stats: dict) -> str:
    """Format a stats dict as a readable block for injection into LLM prompts."""
    lines = []

    if stats["accuracy_pct"] is not None:
        lines.append(
            f"Overall accuracy: {stats['accuracy_pct']}% "
            f"({stats['correct']}/{stats['evaluated']} evaluated, "
            f"{stats['total']} total decisions)"
        )
    else:
        lines.append(f"No evaluated decisions yet ({stats['total']} total)")

    if stats["by_decision_type"]:
        lines.append("\nAccuracy by decision type:")
        for dec_type, counts in sorted(stats["by_decision_type"].items()):
            c = counts.get("correct", 0)
            w = counts.get("wrong", 0)
            n = c + w
            pct = round(c / n * 100, 1) if n else 0
            lines.append(f"  {dec_type}: {pct}% ({c}/{n})")

    # Only show context breakdowns with at least 2 values and sufficient volume
    for field, values in sorted(stats["by_context_field"].items()):
        meaningful = [
            (v, c) for v, c in values.items()
            if c.get("correct", 0) + c.get("wrong", 0) >= 3
        ]
        if len(meaningful) < 2:
            continue
        lines.append(f"\nAccuracy by {field}:")
        for val, counts in sorted(meaningful, key=lambda x: x[0]):
            c = counts.get("correct", 0)
            w = counts.get("wrong", 0)
            n = c + w
            pct = round(c / n * 100, 1) if n else 0
            lines.append(f"  {val}: {pct}% ({c}/{n})")

    return "\n".join(lines)


def error_rate(window_hours: int = 24) -> float | None:
    """
    Return error rate (0.0–1.0) over the window.
    Returns None if fewer than 5 evaluated decisions — not enough signal.
    """
    s = compute(window_hours)
    if s["evaluated"] < 5:
        return None
    return s["wrong"] / s["evaluated"]
