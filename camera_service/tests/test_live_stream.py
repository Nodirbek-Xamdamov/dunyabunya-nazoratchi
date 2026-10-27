import json
import time

import cv2
import numpy as np

from camsvc import live_stream


def test_stream_only_while_a_fresh_request_names_a_static_kamera_folder(tmp_path):
    folder = tmp_path / "static" / "kamera"
    folder.mkdir(parents=True)
    watch = tmp_path / "watch.json"
    assert live_stream.watched_dir(watch_file=watch) is None  # nobody asked
    watch.write_text(json.dumps({"ts": time.time(), "dir": str(folder)}), "utf-8")
    assert live_stream.watched_dir(watch_file=watch) == folder
    assert live_stream.watched_dir(time.time() + 10, watch) is None  # the dashboard stopped asking
    watch.write_text(json.dumps({"ts": time.time(), "dir": str(tmp_path)}), "utf-8")
    assert live_stream.watched_dir(watch_file=watch) is None  # any other folder is refused


def test_stream_frame_is_scaled_down(tmp_path):
    assert live_stream.write_stream(tmp_path, np.zeros((1080, 1920, 3), np.uint8))
    data = np.frombuffer((tmp_path / "stream.jpg").read_bytes(), np.uint8)  # cv2.imread fails on Cyrillic paths
    assert cv2.imdecode(data, cv2.IMREAD_COLOR).shape[:2] == (720, 1280)
