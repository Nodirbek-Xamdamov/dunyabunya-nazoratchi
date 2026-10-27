"""Desk presence state machine, one per desk. Pure logic without I/O, so it is easy to test.

Input: for each processed frame time t (seconds; UTC epoch live, video time on clips) whether
someone counts as "at this desk" (anchor point inside the zone, confidence >= threshold).
Output: events for the database.

Rules (every number is a setting):
- SITTING starts when, within the last `sit_window_s` (10 s), the desk was occupied in at least
  `sit_ratio` (70%) of the processed frames. The start is moved back to the first frame of that
  window in which the person was seen.
- SITTING ends only after `away_grace_s` without the person (user's choice: 10 min); the end is
  the last time the person was seen. Shorter absences (occlusion, behind the monitor, a quick
  walk) stay inside the interval.
- No frames for longer than `gap_s` = no data: an open interval is closed at the last time the
  person was seen and the gap is reported, so an outage is never counted as present or absent.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class PresenceParams:
    sit_window_s: float = 10.0
    sit_ratio: float = 0.70
    away_grace_s: float = 600.0
    gap_s: float = 60.0
    min_coverage: float = 0.6  # the window must span at least 60% of sit_window_s before deciding
    heartbeat_s: float = 30.0  # how often an open interval reports "still seen"


@dataclass(frozen=True)
class Opened:
    start: float


@dataclass(frozen=True)
class Seen:
    start: float
    last_seen: float


@dataclass(frozen=True)
class Closed:
    start: float
    end: float
    reason: str  # "away" | "no_data" | "stopped"


@dataclass(frozen=True)
class Gap:
    start: float
    end: float


class DeskPresence:
    def __init__(self, params: PresenceParams | None = None):
        self.p = params or PresenceParams()
        self._window: deque[tuple[float, bool]] = deque()
        self.sitting = False
        self.start: float | None = None
        self.last_seen: float | None = None
        self.last_t: float | None = None
        self._last_beat = 0.0

    def resume(self, start: float, last_seen: float) -> None:
        """Continue an interval that was open when the service stopped briefly."""
        self.sitting, self.start, self.last_seen = True, start, last_seen
        self.last_t = self._last_beat = last_seen

    def update(self, t: float, present: bool) -> list[Opened | Seen | Closed | Gap]:
        if self.last_t is not None and t <= self.last_t:
            return []
        events: list[Opened | Seen | Closed | Gap] = []
        if self.last_t is not None and t - self.last_t > self.p.gap_s:
            events += self._no_data(self.last_t, t)
        self.last_t = t
        self._window.append((t, present))
        while self._window and self._window[0][0] < t - self.p.sit_window_s:
            self._window.popleft()
        if not self.sitting:
            if present and self._window_says_sitting(t):
                self.start = next(ts for ts, seen in self._window if seen)
                self.last_seen = t
                self.sitting = True
                self._last_beat = t
                events.append(Opened(self.start))
            return events
        if present:
            self.last_seen = t
            if t - self._last_beat >= self.p.heartbeat_s:
                self._last_beat = t
                events.append(Seen(self.start, t))
        elif t - self.last_seen >= self.p.away_grace_s:
            events.append(self._close("away"))
        return events

    def idle(self, t: float) -> list[Closed]:
        """No frames are coming (camera or decoding down): once the silence is longer than gap_s, close an
        open interval at the last time the person was seen. The Gap itself is reported when frames return."""
        if self.sitting and self.last_t is not None and t - self.last_t > self.p.gap_s:
            return [self._close("no_data")]
        return []

    def stop(self) -> list[Closed]:
        """End of a clip: close an open interval at the last time the person was seen."""
        return [self._close("stopped")] if self.sitting else []

    def away_for(self, t: float) -> float | None:
        """Seconds since the person was last seen while the interval is still open."""
        return t - self.last_seen if self.sitting and self.last_seen is not None else None

    # -- internals ----------------------------------------------------------------------------------
    def _window_says_sitting(self, t: float) -> bool:
        if not self._window or t - self._window[0][0] < self.p.min_coverage * self.p.sit_window_s:
            return False
        seen = sum(1 for _, s in self._window if s)
        return seen / len(self._window) >= self.p.sit_ratio

    def _close(self, reason: str) -> Closed:
        event = Closed(self.start, self.last_seen, reason)
        self.sitting, self.start = False, None
        return event

    def _no_data(self, gap_start: float, gap_end: float) -> list[Closed | Gap]:
        events: list[Closed | Gap] = []
        if self.sitting:
            events.append(self._close("no_data"))
        self._window.clear()
        events.append(Gap(gap_start, gap_end))
        return events
