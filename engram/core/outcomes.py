"""Built-in outcome assessment strategies."""
from __future__ import annotations
import importlib
import json
import logging

from engram.schema import OutcomeConfig

logger = logging.getLogger(__name__)


def assess(decision: dict, cfg: OutcomeConfig) -> str | None:
    """
    Assess outcome for a recorded decision.

    Returns 'correct' | 'wrong' | 'inconclusive' | None (pending / not evaluable).
    """
    if cfg.strategy == "binary":
        # Outcome already pushed explicitly by host via eng.outcome()
        return decision.get("outcome")

    if cfg.strategy == "score":
        return _score(decision, cfg)

    if cfg.strategy == "custom":
        return _custom(decision, cfg)

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


