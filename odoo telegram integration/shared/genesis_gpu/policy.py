"""Pure scheduling decision (no I/O), so it can be tested with any clock (CONTRACTS §3, D-30, D-14)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

DAY = 24 * 60
MODES = ("timeslice", "coresident", "dedicated")
ALL_CLASSES = frozenset({"none", "llm", "vision"})


def parse_window(text: str) -> tuple[int, int]:
    """'10:00-22:00' -> (600, 1320); end 00:00 means midnight at the end of the day."""
    a, b = text.split("-")
    start = int(a[:2]) * 60 + int(a[3:5])
    end = int(b[:2]) * 60 + int(b[3:5])
    return start, (DAY if end == 0 else end)


def in_window(minute: int, win: tuple[int, int]) -> bool:
    start, end = win
    return start <= minute < end if start < end else (minute >= start or minute < end)


def minutes_left(minute: int, win: tuple[int, int]) -> int:
    start, end = win
    return (end - minute) % DAY or DAY


@dataclass(frozen=True)
class Decision:
    mode: str
    window: str | None          # timeslice: 'llm' | 'vision' | 'llm_idle' (idle camera window lent to Gemma); else None
    guard: bool                 # within the guard minutes before the GPU really changes hands
    target: frozenset[str]      # gpu classes that may be claimed (before model/VRAM checks)
    want_llm: bool              # the LLM model should be resident


def decide(now_local: datetime, mode: str, llm_window: str, vision_window: str, guard_minutes: int, *,
           vision_wanted: bool = True, vision_active: bool = False, idle_to_llm: bool = False) -> Decision:
    """D-30 timeslice: Telegram (llm) and camera (vision) windows. With `idle_to_llm` the camera window is lent to
    Gemma while no camera work is wanted (`vision_wanted`: jobs waiting/running, plus the gate's idle grace) and
    taken back when it is; a camera job arriving in the last guard minutes of the window does not take it back.
    The guard before the end of the Telegram window applies only if the camera will really take the GPU.
    `vision_active`: the GPU is given to the camera right now (previous decision was 'vision').
    coresident (D-14 fallback) and dedicated (second GPU, D-30): every class, Gemma always resident."""
    if mode in ("coresident", "dedicated"):
        return Decision(mode, None, False, ALL_CLASSES, True)
    if mode != "timeslice":
        raise ValueError(f"unknown gpu mode: {mode}")
    minute = now_local.hour * 60 + now_local.minute
    llm_w, vis_w = parse_window(llm_window), parse_window(vision_window)
    if in_window(minute, llm_w):
        guard = minutes_left(minute, llm_w) <= guard_minutes and (vision_wanted or not idle_to_llm)
        return Decision(mode, "llm", guard, frozenset({"none"} if guard else {"none", "llm"}), True)
    if in_window(minute, vis_w):
        guard = minutes_left(minute, vis_w) <= guard_minutes
        if idle_to_llm and (not vision_wanted or (guard and not vision_active)):
            return Decision(mode, "llm_idle", False, frozenset({"none", "llm"}), True)
        return Decision(mode, "vision", guard, frozenset({"none"} if guard else {"none", "vision"}), False)
    return Decision(mode, None, False, frozenset({"none"}), False)
