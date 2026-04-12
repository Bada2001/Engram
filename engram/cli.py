"""CLI — engram init / run / review / apply / reject / report / diary / propose / extract."""
from __future__ import annotations
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import click

SCHEMA_TEMPLATE = """\
# engram.yaml — configure Engram for your project

name: "MyProject"
domain: |
  Describe your system here. What decisions does it make?
  What domain does it operate in? What context is relevant?
  The richer this description, the better the proposals will be.

# How to assess whether a decision was correct
outcome:
  strategy: binary          # binary | price_movement | score | custom
  #
  # binary: host calls eng.outcome(decision_id, "correct"|"wrong"|"inconclusive")
  #
  # price_movement options:
  # threshold_pct: 0.5         # % price move required to call correct/wrong
  # window_hours: 6            # hours after decision to evaluate
  # min_window_hours: 4        # minimum elapsed before evaluating
  # instrument_field: instrument  # context key holding the instrument name
  #
  # score options:
  # score_field: score
  # score_threshold: 0.5
  #
  # custom option:
  # assessor: "myapp.outcomes.assess"  # dotted path to callable

# Tunable parameters (used as context for proposal generation)
parameters:
  - name: example_param
    type: float
    description: "What this parameter controls and what it affects"

# Proposal categories (tailor to your domain)
proposal_categories:
  - prompt
  - threshold
  - parameter
  - timing
  - architecture
  - cost

# Schedule — times as 'HH:MM Timezone'
schedule:
  diary_time: "17:30 UTC"       # daily diary (match your system's day-end)
  propose_time: "01:00 UTC"     # nightly proposals (runs after diary)
  checkpoint_day: sunday        # weekly checkpoint + lesson extraction
  lesson_extraction: weekly     # weekly | on_error (triggers when 3+ wrong calls)

# LLM settings
llm:
  model: claude-sonnet-4-6
  max_tokens: 2000
"""


def _load(schema_path: str, db_path: str | None = None):
    """Load Engram, resolving db_path relative to schema if not given."""
    from engram import Engram
    p = Path(schema_path)
    if not p.exists():
        click.echo(f"Error: schema not found: {schema_path}", err=True)
        sys.exit(1)
    resolved_db = db_path or str(p.parent / "engram.db")
    return Engram(schema=str(p), db_path=resolved_db)


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option()
def cli():
    """Engram — agnostic learning layer for decision systems."""


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--path", default=".", help="Directory to initialise")
def init(path: str):
    """Scaffold engram.yaml in the current (or specified) directory."""
    target = Path(path) / "engram.yaml"
    if target.exists():
        click.echo(f"engram.yaml already exists at {target}")
        return
    target.write_text(SCHEMA_TEMPLATE)
    click.echo(f"Created {target}")
    click.echo("Edit it to describe your system, then integrate with eng.observe() / eng.price().")
    click.echo("Start the scheduler with: engram run")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--schema", default="engram.yaml", show_default=True)
@click.option("--db", default=None, help="Path to engram.db")
def run(schema: str, db: str | None):
    """Start the background scheduler (blocking). Use for standalone sidecar mode."""
    import time
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)-24s  %(levelname)s  %(message)s",
    )
    eng = _load(schema, db)
    eng.start()
    click.echo(f"Engram running for '{eng._schema.name}'. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        eng.stop()
        click.echo("\nStopped.")


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--schema", default="engram.yaml", show_default=True)
@click.option("--db", default=None)
@click.option("--all", "show_all", is_flag=True, help="Include applied/rejected")
def review(schema: str, db: str | None, show_all: bool):
    """List improvement proposals."""
    import engram.core.db as _db
    _load(schema, db)

    if show_all:
        rows = _db.fetchall("SELECT * FROM proposals ORDER BY written_ts DESC LIMIT 100")
    else:
        rows = _db.fetchall(
            "SELECT * FROM proposals WHERE status = 'pending' "
            "ORDER BY priority DESC, written_ts DESC"
        )

    if not rows:
        click.echo("No proposals." if show_all else "No pending proposals.")
        return

    _priority = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda r: (_priority.get(r.get("priority", "medium"), 1), r.get("written_ts", "")))

    for r in rows:
        sym    = {"pending": "○", "applied": "✓", "rejected": "✗"}.get(r["status"], "?")
        pri    = r.get("priority", "medium").upper()
        cat    = r.get("category", "?")
        click.echo(f"\n[{r['id']}] {sym} [{pri}] [{cat}] {r['title']}")
        click.echo(f"     Date: {r.get('analysis_date','?')}  Status: {r['status']}")
        click.echo(f"     Problem: {(r.get('problem') or '')[:140]}")
        try:
            evidence = json.loads(r.get("evidence") or "[]")
            for ev in evidence[:2]:
                click.echo(f"       · {str(ev)[:120]}")
        except Exception:
            pass
        click.echo(f"     Proposal: {(r.get('proposal') or '')[:220]}")
        if r.get("affected_files"):
            click.echo(f"     Files: {r['affected_files']}")
        if r.get("user_notes"):
            click.echo(f"     Notes: {r['user_notes']}")


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("proposal_id", type=int)
@click.option("--schema", default="engram.yaml", show_default=True)
@click.option("--db", default=None)
@click.option("--notes", default="", help="Implementation notes")
def apply(proposal_id: int, schema: str, db: str | None, notes: str):
    """Mark a proposal as applied."""
    import engram.core.db as _db
    _load(schema, db)
    now = datetime.now(timezone.utc).isoformat()
    _db.execute(
        "UPDATE proposals SET status = 'applied', implemented_ts = ?, user_notes = ? WHERE id = ?",
        (now, notes, proposal_id),
    )
    click.echo(f"Proposal {proposal_id} marked as applied.")


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("proposal_id", type=int)
@click.option("--schema", default="engram.yaml", show_default=True)
@click.option("--db", default=None)
@click.option("--notes", default="", help="Reason for rejection")
def reject(proposal_id: int, schema: str, db: str | None, notes: str):
    """Reject a proposal."""
    import engram.core.db as _db
    _load(schema, db)
    _db.execute(
        "UPDATE proposals SET status = 'rejected', user_notes = ? WHERE id = ?",
        (notes, proposal_id),
    )
    click.echo(f"Proposal {proposal_id} rejected.")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--schema", default="engram.yaml", show_default=True)
@click.option("--db", default=None)
@click.option("--days", default=7, show_default=True, help="Lookback window")
def report(schema: str, db: str | None, days: int):
    """Show accuracy stats, active lessons, and proposal summary."""
    import engram.core.db as _db
    eng   = _load(schema, db)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    decisions = _db.fetchall(
        "SELECT decision, outcome FROM decisions WHERE ts >= ?", (since,)
    )
    total   = len(decisions)
    correct = sum(1 for d in decisions if d.get("outcome") == "correct")
    wrong   = sum(1 for d in decisions if d.get("outcome") == "wrong")
    pending = total - correct - wrong

    click.echo(f"\n=== Engram: {eng._schema.name} — last {days}d ===")
    click.echo(f"Decisions : {total}  |  Correct: {correct}  |  Wrong: {wrong}  |  Pending: {pending}")
    if correct + wrong:
        click.echo(f"Accuracy  : {correct / (correct + wrong) * 100:.1f}%  (excl. pending)")

    lessons = eng.active_lessons()
    click.echo(f"\nActive lessons ({len(lessons)}):")
    for l in lessons[:10]:
        click.echo(f"  • {l[:130]}")
    if len(lessons) > 10:
        click.echo(f"  … and {len(lessons) - 10} more")

    prop_stats = _db.fetchall(
        "SELECT status, COUNT(*) as n FROM proposals GROUP BY status"
    )
    click.echo("\nProposals:")
    for p in prop_stats:
        click.echo(f"  {p['status']}: {p['n']}")

    top_pending = _db.fetchall(
        "SELECT id, priority, title FROM proposals WHERE status = 'pending' "
        "ORDER BY priority DESC, written_ts DESC LIMIT 5"
    )
    if top_pending:
        click.echo("\nTop pending:")
        for p in top_pending:
            click.echo(f"  [{p['id']}] [{p['priority']}] {p['title'][:90]}")


# ---------------------------------------------------------------------------
# Manual triggers
# ---------------------------------------------------------------------------

@cli.command(name="diary")
@click.option("--schema", default="engram.yaml", show_default=True)
@click.option("--db", default=None)
def run_diary(schema: str, db: str | None):
    """Manually run the daily diary."""
    import logging
    logging.basicConfig(level=logging.INFO)
    eng = _load(schema, db)
    eng.run_diary()
    click.echo("Diary written.")


@cli.command(name="propose")
@click.option("--schema", default="engram.yaml", show_default=True)
@click.option("--db", default=None)
def run_propose(schema: str, db: str | None):
    """Manually run proposal generation."""
    import logging
    logging.basicConfig(level=logging.INFO)
    eng = _load(schema, db)
    eng.run_proposals()
    click.echo("Proposals generated.")


@cli.command(name="extract")
@click.option("--schema", default="engram.yaml", show_default=True)
@click.option("--db", default=None)
def run_extract(schema: str, db: str | None):
    """Manually run lesson extraction."""
    import logging
    logging.basicConfig(level=logging.INFO)
    eng = _load(schema, db)
    eng.run_extract()
    click.echo("Lessons extracted.")
