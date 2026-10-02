from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


class UpdateWindow:
    """Local wall-clock window for beginning updates, with DST-aware waits."""

    def __init__(self, start: str | None, end: str | None, tz: str):
        self.start = time.fromisoformat(start) if start else None
        self.end = time.fromisoformat(end) if end else None
        self.tz = ZoneInfo(tz)

    def contains_time(self, value: time) -> bool:
        if self.start is None or self.end is None:
            return True
        if self.start < self.end:
            return self.start <= value < self.end
        return value >= self.start or value < self.end

    def is_open(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(self.tz)
        return self.contains_time(now.astimezone(self.tz).time())

    def next_open_delay(self, now: datetime) -> float:
        if self.is_open(now):
            return 0.0
        now = now.astimezone(self.tz)
        candidates = []
        for days in range(3):
            wall_start = datetime.combine(now.date() + timedelta(days=days), self.start)
            for fold in (0, 1):
                candidate = wall_start.replace(tzinfo=self.tz, fold=fold)
                # Round-trip imaginary spring times; consider both autumn folds.
                candidate = datetime.fromtimestamp(candidate.timestamp(), self.tz)
                if candidate.timestamp() > now.timestamp() and self.is_open(candidate):
                    candidates.append(candidate.timestamp())
        return min(candidates) - now.timestamp()

    def sleep_duration(self, interval: float, now: datetime | None = None) -> float:
        if self.start is None:
            return interval
        now = now or datetime.now(self.tz)
        if not self.is_open(now):
            return self.next_open_delay(now)
        target = datetime.fromtimestamp(now.timestamp() + interval, self.tz)
        return interval + self.next_open_delay(target)
