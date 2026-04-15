"""Schema loader and validator for engram.yaml."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class OutcomeConfig:
    strategy: str = "binary"
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
class CodebaseConfig:
    dir: str = ""                  # empty = disabled
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=lambda: [
        ".venv", "__pycache__", ".git", "*.pyc", "*.pyo", "node_modules",
    ])
    max_chars: int = 8000          # total character budget across all files


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
    codebase: CodebaseConfig = field(default_factory=CodebaseConfig)


def _parse(raw: dict) -> EngramSchema:
    s = EngramSchema()
    s.name   = raw.get("name", "Unnamed")
    s.domain = (raw.get("domain") or "").strip()

    if "outcome" in raw:
        o = raw["outcome"]
        s.outcome = OutcomeConfig(
            strategy        = o.get("strategy", "binary"),
            score_field     = o.get("score_field", "score"),
            score_threshold = float(o.get("score_threshold", 0.5)),
            assessor        = o.get("assessor", ""),
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

    if "codebase" in raw:
        cb = raw["codebase"]
        s.codebase = CodebaseConfig(
            dir       = cb.get("dir", ""),
            include   = cb.get("include", []),
            exclude   = cb.get("exclude", CodebaseConfig().exclude),
            max_chars = int(cb.get("max_chars", 8000)),
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
