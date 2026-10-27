import time

import numpy as np

from camsvc import live_relay
from camsvc.stream import Frame


class FakeReader:
    def __init__(self):
        self.seq, self.stopped = 0, False

    def wait_newer(self, seq, timeout):
        self.seq += 1
        return Frame(image=np.zeros((1080, 1920, 3), np.uint8), seq=self.seq, wall=time.time(), mono=time.monotonic())

    def stop(self):
        self.stopped = True


def test_relay_is_connected_to_the_camera_only_while_the_dashboard_asks(tmp_path):
    folder = tmp_path / "static" / "kamera"
    folder.mkdir(parents=True)
    asked = {"on": False}
    readers = []

    def open_reader():
        readers.append(FakeReader())
        return readers[-1]

    relay = live_relay.Relay(open_reader, watch=lambda now: folder if asked["on"] else None)
    relay.step(100.0)
    assert not readers and not (folder / "stream.jpg").exists()  # nobody watches: no camera connection
    asked["on"] = True
    relay.step(101.0)
    assert len(readers) == 1 and (folder / "stream.jpg").exists()
    asked["on"] = False
    relay.step(102.0)
    assert not readers[0].stopped  # kept for a while, so coming back is instant
    relay.step(101.0 + live_relay.IDLE_S + 1)
    assert readers[0].stopped and relay.reader is None
