from camsvc.pipeline import tracker_config


def test_tracker_memory_is_30_seconds_at_any_speed(tmp_path):
    for fps, frames in ((5, 150), (20, 600), (0.5, 30)):
        text = open(tracker_config(fps, tmp_path), encoding="utf-8").read()
        assert f"\ntrack_buffer: {frames}\n" in text
        assert "tracker_type: bytetrack" in text
