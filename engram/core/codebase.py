"""Read source files from the observed codebase for inclusion in proposal context."""
from __future__ import annotations
import fnmatch
import logging
from pathlib import Path

import engram.core.db as db
from engram.schema import CodebaseConfig

logger = logging.getLogger(__name__)


def _is_excluded(path: Path, root: Path, exclude: list[str]) -> bool:
    """Return True if any exclude pattern matches any part of the path."""
    rel = str(path.relative_to(root))
    for pat in exclude:
        # Match against the full relative path and each individual part
        if fnmatch.fnmatch(rel, f"*{pat}*"):
            return True
        if any(fnmatch.fnmatch(part, pat) for part in path.parts):
            return True
    return False


def _read_file(path: Path, budget: int) -> tuple[str, int]:
    """Read a file up to the character budget. Returns (content, chars_used)."""
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
        if len(content) > budget:
            content = content[:budget] + "\n... (truncated)"
        return content, len(content)
    except Exception as e:
        logger.warning("Codebase: could not read '%s': %s", path, e)
        return "", 0


def read_context(cfg: CodebaseConfig, extra_files: list[str] | None = None) -> str:
    """
    Read source files from the configured codebase directory and return a
    formatted context block for injection into LLM prompts.

    Files are sourced from:
      1. cfg.include glob patterns (relative to cfg.dir)
      2. extra_files — additional paths (e.g. affected_files from recent proposals)

    Total output is capped at cfg.max_chars.
    Returns an empty string if cfg.dir is not set or doesn't exist.
    """
    if not cfg.dir:
        return ""

    root = Path(cfg.dir).resolve()
    if not root.exists():
        logger.warning("Codebase: dir '%s' does not exist", cfg.dir)
        return ""

    # Collect candidate files
    candidates: list[Path] = []

    for pattern in cfg.include:
        try:
            # If the pattern is a plain directory path, expand to all files inside it
            candidate = root / pattern
            if candidate.is_dir():
                candidates.extend(sorted(p for p in candidate.iterdir() if p.is_file()))
            else:
                matched = sorted(root.glob(pattern))
                candidates.extend(p for p in matched if p.is_file())
        except Exception as e:
            logger.warning("Codebase: bad include pattern '%s': %s", pattern, e)

    if extra_files:
        for f in extra_files:
            p = root / f.strip()
            if p.is_dir():
                candidates.extend(sorted(q for q in p.iterdir() if q.is_file()))
            elif p.is_file():
                candidates.append(p)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[Path] = []
    for p in candidates:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(p)

    if not unique:
        return ""

    # Read files within the character budget
    sections: list[str] = []
    remaining = cfg.max_chars

    for path in unique:
        if remaining <= 0:
            break
        if _is_excluded(path, root, cfg.exclude):
            logger.debug("Codebase: skipping excluded file '%s'", path)
            continue

        rel     = path.relative_to(root)
        content, used = _read_file(path, remaining)
        if not content:
            continue

        sections.append(f"### {rel}\n```\n{content}\n```")
        remaining -= used

    if not sections:
        return ""

    skipped = len(unique) - len(sections)
    footer  = f"\n_(showing {len(sections)} files" + (f", {skipped} skipped — budget exhausted" if skipped else "") + ")_"
    return "\n\n".join(sections) + footer


def recent_affected_files(limit: int = 20) -> list[str]:
    """
    Return a deduplicated list of file paths from the affected_files column
    of the most recent proposals. Used to auto-include relevant files in context.
    """
    rows = db.fetchall(
        "SELECT affected_files FROM proposals "
        "WHERE affected_files IS NOT NULL AND affected_files != '' "
        "ORDER BY written_ts DESC LIMIT ?",
        (limit,),
    )
    seen:  set[str]  = set()
    files: list[str] = []
    for row in rows:
        for f in (row["affected_files"] or "").split(","):
            f = f.strip()
            if f and f not in seen:
                seen.add(f)
                files.append(f)
    return files
