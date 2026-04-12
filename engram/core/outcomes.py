"""Built-in outcome assessment strategies."""
from __future__ import annotations
import importlib
import json
import logging
from datetime import datetime, timedelta

import engram.core.db as db
from engram.schema import OutcomeConfig

logger = logging.getLogger(__name__)

# Decisions with these values are never evaluated
NEUTRAL = {"WAIT", "HOLD", "NEUTRAL", "SKIP", "PASS"}


def assess(decision: dict, cfg: OutcomeConfig) -> str | None:
    """
    Assess outcome for a recorded decision.

    Returns 'correct' | 'wrong' | 'inconclusive' | None (pending / not evaluable).

    NEUTRAL decisions (WAIT, HOLD, etc.) always return None.
    """
    if decision["decision"].upper() in NEUTRAL:
        return None

    if cfg.strategy == "binary":
        # Outcome already pushed explicitly by host via eng.outcome()
        return decision.get("outcome")

    if cfg.strategy == "price_movement":
        return _price_movement(decision, cfg)

    if cfg.strategy == "score":
        return _score(decision, cfg)

    if cfg.strategy == "custom":
        return _custom(decision, cfg)

    return None


def _price_movement(decision: dict, cfg: OutcomeConfig) -> str | None:
    context = json.loads(decision.get("context") or "{}")
    instrument = context.get(cfg.instrument_field)
    if not instrument:
        logger.debug(
            "price_movement: no instrument in context field '%s'", cfg.instrument_field
        )
        return None

    decision_ts = decision["ts"]
    eval_target = _add_hours(decision_ts, cfg.window_hours)
    min_eval    = _add_hours(decision_ts, cfg.min_window_hours)

    rows = db.fetchall(
        "SELECT price, ts FROM prices WHERE instrument = ? AND ts >= ? ORDER BY ts ASC LIMIT 200",
        (instrument, decision_ts),
    )
    if len(rows) < 2:
        return None

    price_entry = rows[0]["price"]
    if price_entry == 0:
        return None

    price_eval = None
    for r in rows:
        if r["ts"] >= eval_target:
            price_eval = r["price"]
            break

    if price_eval is None:
        latest = rows[-1]
        if latest["ts"] < min_eval:
            return None  # still too early
        price_eval = latest["price"]

    change_pct = (price_eval - price_entry) / price_entry * 100
    dec        = decision["decision"].upper()

    if dec in ("BUY", "LONG", "UP", "BULLISH"):
        if change_pct >= cfg.threshold_pct:
            return "correct"
        if change_pct <= -cfg.threshold_pct:
            return "wrong"
        return "inconclusive"

    if dec in ("SELL", "SHORT", "EXIT", "DOWN", "BEARISH"):
        if change_pct <= -cfg.threshold_pct:
            return "correct"
        if change_pct >= cfg.threshold_pct:
            return "wrong"
        return "inconclusive"

    return None


def _score(decision: dict, cfg: OutcomeConfig) -> str | None:
    context = json.loads(decision.get("context") or "{}")
    score   = context.get(cfg.score_field)
    if score is None:
        return None
    return "correct" if float(score) >= cfg.score_threshold else "wrong"


def _custom(decision: dict, cfg: OutcomeConfig) -> str | None:
    if not cfg.assessor:
        return None
    try:
        module_path, fn_name = cfg.assessor.rsplit(".", 1)
        module = importlib.import_module(module_path)
        fn     = getattr(module, fn_name)
        return fn(decision)
    except Exception as e:
        logger.error("Custom assessor '%s' failed: %s", cfg.assessor, e)
        return None


def _add_hours(ts_iso: str, hours: int) -> str:
    dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    return (dt + timedelta(hours=hours)).isoformat()
