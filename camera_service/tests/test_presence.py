"""Synthetic sequences for the desk state machine (stage 4 acceptance tests)."""

import random

from camsvc.presence import Closed, DeskPresence, Gap, Opened, PresenceParams, Seen

FPS = 5
STEP = 1 / FPS


def feed(machine, pattern, t0=0.0):
    """pattern: list of (seconds, present). Returns all events and the end time."""
    events, t = [], t0
    for seconds, present in pattern:
        for _ in range(int(round(seconds * FPS))):
            t += STEP
            events += machine.update(round(t, 3), present)
    return events, t


def intervals(events):
    return [(round(e.start), round(e.end), e.reason) for e in events if isinstance(e, Closed)]


def test_short_absence_is_not_a_break():
    m = DeskPresence(PresenceParams(away_grace_s=180))
    events, _ = feed(m, [(5, False), (600, True), (60, False), (600, True), (200, False)])
    assert intervals(events) == [(5, 1265, "away")]


def test_five_minutes_away_is_a_break_with_3_minute_grace():
    m = DeskPresence(PresenceParams(away_grace_s=180))
    events, _ = feed(m, [(600, True), (300, False), (600, True), (200, False)])
    assert intervals(events) == [(0, 600, "away"), (900, 1500, "away")]


def test_users_10_minute_grace():
    m = DeskPresence(PresenceParams(away_grace_s=600))
    events, _ = feed(m, [(600, True), (540, False), (600, True), (660, False), (300, True), (700, False)])
    # 9 minutes away stays inside the interval, 11 minutes away is a break
    assert intervals(events) == [(0, 1740, "away"), (2400, 2700, "away")]


def test_start_moves_back_to_first_seen():
    m = DeskPresence()
    events, _ = feed(m, [(100, False), (30, True)])
    opened = [e for e in events if isinstance(e, Opened)]
    assert len(opened) == 1 and abs(opened[0].start - 100.2) < 0.01


def test_passer_by_is_not_counted():
    m = DeskPresence()
    events, _ = feed(m, [(60, False), (3, True), (60, False), (4, True), (60, False)])
    assert not [e for e in events if isinstance(e, (Opened, Closed))]


def test_flickering_detection_while_sitting_stays_one_interval():
    rng = random.Random(1)
    m = DeskPresence(PresenceParams(away_grace_s=180))
    pattern = [(STEP, rng.random() > 0.25) for _ in range(3600 * FPS)]  # one hour, 25% of frames missed
    pattern = [(10, True)] + pattern + [(400, False)]
    events, _ = feed(m, pattern)
    closed = intervals(events)
    assert len(closed) == 1 and closed[0][1] >= 3605


def test_service_outage_is_no_data():
    m = DeskPresence()
    events, t = feed(m, [(600, True)])
    events += m.update(t + 300, True)  # 5 minutes without any frame
    more, _ = feed(m, [(600, True)], t0=t + 300)
    events += more + m.stop()
    gaps = [e for e in events if isinstance(e, Gap)]
    assert intervals(events) == [(0, 600, "no_data"), (900, 1500, "stopped")]
    assert len(gaps) == 1 and round(gaps[0].end - gaps[0].start) == 300


def test_camera_silence_closes_interval_before_frames_return():
    m = DeskPresence()
    events, t = feed(m, [(300, True)])
    assert m.idle(t + 30) == []  # 30 s of silence: still open
    closed = m.idle(t + 61)
    assert intervals(closed) == [(0, 300, "no_data")]
    more = m.update(t + 400, True)  # frames come back
    assert [round(g.end - g.start) for g in more if isinstance(g, Gap)] == [400]


def test_heartbeat_while_sitting():
    m = DeskPresence()
    events, _ = feed(m, [(125, True)])
    seen = [e for e in events if isinstance(e, Seen)]
    assert len(seen) == 3 and all(e.start == seen[0].start for e in seen)


def test_resume_after_short_restart_keeps_interval():
    m = DeskPresence()
    m.resume(start=1000.0, last_seen=1500.0)
    events, _ = feed(m, [(30, True), (700, False)], t0=1520.0)
    assert intervals(events) == [(1000, 1550, "away")]
