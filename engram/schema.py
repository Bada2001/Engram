"""Schema loader and validator for engram.yaml."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class OutcomeConfig:
    strategy: str = "binary"
    # price_movement
    threshold_pct: float = 0.5
    window_hours: int = 6
    min_window_hours: int = 4
    instrument_field: str = "instrument"
    # score
    score_field: str = "score"
    score_threshold: float = 0.5
    # custom
    assessor: str = ""


@dataclass
class ScheduleConfig:
    diary_time: str = "17:30 UTC"       # 'HH:MM Timezone'
    propose_time: str = "01:00 UTC"
    checkpoint_day: str = "sunday"
    lesson_extraction: str = "weekly"   # weekly | on_error


@dataclass
class LLMConfig:
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 2000


@dataclass
class EngramSchema:
    name: str = "Unnamed"
    domain: str = ""
    outcome: OutcomeConfig = field(default_factory=OutcomeConfig)
    parameters: list[dict] = field(default_factory=list)
    proposal_categories: list[str] = field(default_factory=lambda: [
        "prompt", "threshold", "parameter", "timing", "architecture", "cost",
    ])
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)


def _parse(raw: dict) -> EngramSchema:
    s = EngramSchema()
    s.name   = raw.get("name", "Unnamed")
    s.domain = (raw.get("domain") or "").strip()

    if "outcome" in raw:
        o = raw["outcome"]
        s.outcome = OutcomeConfig(
            strategy         = o.get("strategy", "binary"),
            threshold_pct    = float(o.get("threshold_pct", 0.5)),
            window_hours     = int(o.get("window_hours", 6)),
            min_window_hours = int(o.get("min_window_hours", 4)),
            instrument_field = o.get("instrument_field", "instrument"),
            score_field      = o.get("score_field", "score"),
            score_threshold  = float(o.get("score_threshold", 0.5)),
            assessor         = o.get("assessor", ""),
        )

    s.parameters          = raw.get("parameters", [])
    s.proposal_categories = raw.get("proposal_categories", s.proposal_categories)

    if "schedule" in raw:
        sc = raw["schedule"]
        s.schedule = ScheduleConfig(
            diary_time        = sc.get("diary_time", "17:30 UTC"),
            propose_time      = sc.get("propose_time", "01:00 UTC"),
            checkpoint_day    = sc.get("checkpoint_day", "sunday").lower(),
            lesson_extraction = sc.get("lesson_extraction", "weekly"),
        )

    if "llm" in raw:
        ll = raw["llm"]
        s.llm = LLMConfig(
            model      = ll.get("model", "claude-sonnet-4-6"),
            max_tokens = int(ll.get("max_tokens", 2000)),
        )

    return s


def load(path: str | Path) -> EngramSchema:
    """Load and parse an engram.yaml file."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return _parse(raw)


def from_dict(d: dict) -> EngramSchema:
    """Build an EngramSchema directly from a dict."""
    return _parse(d)
