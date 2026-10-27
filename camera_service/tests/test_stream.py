import time

import numpy as np

from camsvc.stream import Frame, LatestFrameReader


def push(reader, seq, age=0.0):
    reader._push(Frame(image=np.zeros((2, 2, 3), np.uint8), seq=seq, wall=time.time() - age, mono=time.monotonic() - age))


def test_frame_right_behind_another_is_not_lost():
    reader = LatestFrameReader(lambda: "")
    push(reader, 1)
    push(reader, 2)
    assert reader.wait_newer(0, 0, max_lag=0.25).seq == 1
    assert reader.wait_newer(1, 0, max_lag=0.25).seq == 2
    assert reader.wait_newer(0, 0).seq == 2  # without max_lag: always the newest


def test_old_frames_are_skipped():
    reader = LatestFrameReader(lambda: "")
    push(reader, 1, age=1.0)
    push(reader, 2)
    assert reader.wait_newer(0, 0, max_lag=0.25).seq == 2
