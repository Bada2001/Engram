"""Engram — agnostic learning layer for decision systems."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from engram import schema as _schema_mod
import engram.core.db as _db
from engram.core.scheduler import Scheduler
from engram.schema import EngramSchema

__version__ = "0.1.0"
__all__      = ["Engram"]

logger = logging.getLogger(__name__)


class Engram:
    """
    Plug-in learning layer for any decision system.

    Minimal integration::

        from engram import Engram

        eng = Engram("engram.yaml", db_path="engram.db")
        eng.start()   # background scheduler: diary → extract → propose

        # Where a decision is made:
        eng.observe("order-001", decision="BUY", context={"instrument": "brent", "conviction": 0.82})

        # Feed prices (required for price_movement outcome strategy):
        eng.price("brent", 87.50)

        # Push outcome explicitly (for binary strategy):
        eng.outcome("order-001", "correct")

        # Retrieve active lessons for injection into prompts:
        for lesson in eng.active_lessons():
            print(lesson)
    """

    def __init__(
        self,
        schema: str | Path | dict | EngramSchema,
        db_path: str = "engram.db",
    ):
        if isinstance(schema, EngramSchema):
            self._schema = schema
        elif isinstance(schema, dict):
            self._schema = _schema_mod.from_dict(schema)
        else:
            self._schema = _schema_mod.load(schema)

        _db.configure(db_path)
        _db.init()

        self._scheduler = Scheduler(self._schema)
        logger.info("Engram initialised: '%s' (db=%s)", self._schema.name, db_path)

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def observe(
        self,
        decision_id: str,
        decision: str,
        context: dict | None = None,
        ts: str | None = None,
    ) -> None:
        """
        Record a decision made by the host system.

        decision_id: unique identifier for this decision (host-provided)
        decision:    the decision value (e.g. "BUY", "EXIT", "APPROVE", "REJECT")
        context:     arbitrary dict of context data (passed to outcome assessors + proposals)
        ts:          ISO8601 UTC timestamp; defaults to now
        """
        ts           = ts or datetime.now(timezone.utc).isoformat()
        context_json = json.dumps(context or {})
        try:
            _db.execute(
                "INSERT OR IGNORE INTO decisions (ts, decision_id, decision, context) "
                "VALUES (?, ?, ?, ?)",
                (ts, decision_id, decision, context_json),
            )
        except Exception as e:
            logger.error("Engram.observe failed: %s", e)

    def outcome(
        self,
        decision_id: str,
        result: str,
        metadata: dict | None = None,
    ) -> None:
        """
        Push an explicit outcome for a recorded decision.
        Use this with strategy=binary or strategy=custom.

        result: 'correct' | 'wrong' | 'inconclusive'
        """
        now = datetime.now(timezone.utc).isoformat()
        _db.execute(
            "UPDATE decisions SET outcome = ?, outcome_ts = ?, outcome_raw = ? "
            "WHERE decision_id = ?",
            (result, now, json.dumps(metadata or {}), decision_id),
        )

    def price(
        self,
        instrument: str,
        price: float,
        ts: str | None = None,
    ) -> None:
        """
        Feed a price data point.
        Required when using strategy=price_movement.

        instrument: matches the value of context[outcome.instrument_field]
        """
        ts = ts or datetime.now(timezone.utc).isoformat()
        _db.execute(
            "INSERT INTO prices (ts, instrument, price) VALUES (?, ?, ?)",
            (ts, instrument, price),
        )

    def active_lessons(self) -> list[str]:
        """
        Return active lesson texts in reverse-chronological order.
        Inject these into your system's prompts to close the learning loop.
        """
        now = datetime.now(timezone.utc).isoformat()
        rows = _db.fetchall(
            "SELECT text FROM lessons "
            "WHERE type = 'lesson' AND (expires_ts IS NULL OR expires_ts > ?) "
            "ORDER BY written_ts DESC",
            (now,),
        )
        return [r["text"] for r in rows]

    # ------------------------------------------------------------------
    # Scheduler lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background scheduler (non-blocking daemon thread)."""
        self._scheduler.start()

    def stop(self) -> None:
        """Stop the background scheduler."""
        self._scheduler.stop()

    # ------------------------------------------------------------------
    # Manual triggers — useful for testing or on-demand runs
    # ------------------------------------------------------------------

    def run_diary(self) -> None:
        """Write today's diary entry immediately."""
        from engram.core import diary
        diary.write(self._schema)

    def run_proposals(self) -> None:
        """Run nightly proposal generation immediately."""
        from engram.core import proposer
        proposer.run(self._schema)

    def run_extract(self) -> None:
        """Run lesson extraction immediately."""
        from engram.core import extractor
        extractor.extract(self._schema)

    def run_checkpoint(self) -> None:
        """Run weekly checkpoint compression immediately."""
        from engram.core import extractor
        extractor.weekly_checkpoint(self._schema)
