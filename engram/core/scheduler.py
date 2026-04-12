"""Background scheduler — diary / extract / propose on configurable schedule."""
from __future__ import annotations
import logging
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from engram.schema import EngramSchema

logger = logging.getLogger(__name__)

_WEEKDAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _parse_time(time_str: str) -> tuple[int, int, str]:
    """
    Parse 'HH:MM Timezone' → (hour, minute, tz_name).
    Falls back to UTC if timezone is omitted or unrecognised.
    """
    parts  = time_str.strip().split(None, 1)
    hm     = parts[0]
    tz_str = parts[1].strip() if len(parts) > 1 else "UTC"
    h, m   = map(int, hm.split(":"))
    return h, m, tz_str


def _in_window(hour: int, minute: int, tz_str: str, window_min: int = 5) -> bool:
    """True if current local time is within window_min minutes of hour:minute."""
    try:
        tz  = ZoneInfo(tz_str)
        now = datetime.now(timezone.utc).astimezone(tz)
        target_total = hour * 60 + minute
        now_total    = now.hour * 60 + now.minute
        return abs(now_total - target_total) < window_min
    except Exception as e:
        logger.warning("Scheduler: cannot parse tz '%s': %s", tz_str, e)
        return False


class Scheduler:
    def __init__(self, schema: EngramSchema):
        self._schema = schema
        self._thread: threading.Thread | None = None
        self._stop   = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="engram-scheduler",
        )
        self._thread.start()
        logger.info("Engram scheduler started for '%s'", self._schema.name)

    def stop(self) -> None:
        self._stop.set()
        logger.info("Engram scheduler stopped")

    def _loop(self) -> None:
        sc = self._schema.schedule

        diary_h,   diary_m,   diary_tz   = _parse_time(sc.diary_time)
        propose_h, propose_m, propose_tz = _parse_time(sc.propose_time)
        checkpoint_weekday = _WEEKDAY_MAP.get(sc.checkpoint_day, 6)

        diary_done_today     = False
        propose_done_today   = False
        checkpoint_done_week = False
        extract_done_week    = False
        last_day             = -1
        last_week            = -1

        while not self._stop.is_set():
            try:
                now      = datetime.now(timezone.utc)
                day      = now.weekday()
                week_num = now.isocalendar()[1]

                if day != last_day:
                    diary_done_today   = False
                    propose_done_today = False
                    last_day = day

                if week_num != last_week:
                    checkpoint_done_week = False
                    extract_done_week    = False
                    last_week = week_num

                # Daily diary
                if not diary_done_today and _in_window(diary_h, diary_m, diary_tz):
                    logger.info("Engram: running daily diary")
                    from engram.core import diary
                    diary.write(self._schema)
                    diary_done_today = True

                # Nightly proposals
                if not propose_done_today and _in_window(propose_h, propose_m, propose_tz):
                    logger.info("Engram: running nightly proposals")
                    from engram.core import proposer
                    proposer.run(self._schema)
                    propose_done_today = True

                # Weekly checkpoint + lesson extraction on checkpoint day
                if day == checkpoint_weekday:
                    if not checkpoint_done_week:
                        logger.info("Engram: running weekly checkpoint")
                        from engram.core import extractor
                        extractor.weekly_checkpoint(self._schema)
                        checkpoint_done_week = True

                    if not extract_done_week and sc.lesson_extraction == "weekly":
                        logger.info("Engram: running weekly lesson extraction")
                        from engram.core import extractor
                        extractor.extract(self._schema)
                        extract_done_week = True

            except Exception as e:
                logger.error("Engram scheduler error: %s", e)

            self._stop.wait(60)
