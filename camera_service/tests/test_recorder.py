import datetime as dt
from pathlib import Path

from camsvc.config import Camera
from camsvc.recorder import RecordConfig, day_dir, ffmpeg_cmd, newest_file, parse_hhmm, record_config

CFG = RecordConfig(Path("E:/Kamera raw data"), "Koridor 2", 7 * 60, 23 * 60)


def test_defaults_are_07_to_23_under_kamera_raw_data():
    cfg = record_config({})
    assert cfg == RecordConfig(Path(r"E:\Kamera raw data"), "Koridor 2", 420, 1380)


def test_settings_from_env_and_a_window_across_midnight_falls_back():
    cfg = record_config({"RECORD_ROOT": "F:/x", "RECORD_CAMERA_NAME": "Ombor", "RECORD_FROM": "06:30",
                         "RECORD_TO": "24:00"})
    assert (cfg.root, cfg.camera_name, cfg.start_min, cfg.end_min) == (Path("F:/x"), "Ombor", 390, 1440)
    assert record_config({"RECORD_FROM": "22:00", "RECORD_TO": "06:00"}).start_min == 420
    assert parse_hhmm("7:5x", 1) == 1 and parse_hhmm("25:00", 1) == 1 and parse_hhmm("07:00", 1) == 420


def test_day_folder_is_year_month_camera_day():
    folder = day_dir(CFG, dt.date(2026, 11, 1))
    assert folder.as_posix() == "E:/Kamera raw data/2026/11-Noyabr/Koridor 2/2026-11-01"


def test_window_and_next_start():
    start, end = CFG.window(dt.date(2026, 10, 26))
    assert (start, end) == (dt.datetime(2026, 10, 26, 7), dt.datetime(2026, 10, 26, 23))
    assert CFG.next_start(dt.datetime(2026, 10, 26, 5, 59)) == dt.datetime(2026, 10, 26, 7)
    assert CFG.next_start(dt.datetime(2026, 10, 31, 23, 0)) == dt.datetime(2026, 11, 1, 7)


def test_ffmpeg_copies_video_into_clock_aligned_10_minute_files():
    cmd = ffmpeg_cmd(Camera("10.0.0.1", 554, "u", "p"), 101, Path("E:/v/2026-10-26"))
    joined = " ".join(cmd)
    assert "-c copy" in joined and "-map 0:v:0" in joined
    assert "-segment_time 600 -segment_atclocktime 1" in joined
    assert cmd[-1].replace("\\", "/") == "E:/v/2026-10-26/%H-%M-%S.mkv"


def test_newest_file_of_the_day(tmp_path):
    for name in ["07-00-00.mkv", "07-10-00.mkv"]:
        (tmp_path / name).write_bytes(b"x" * 3)
    path, size = newest_file(tmp_path)
    assert path.endswith("07-10-00.mkv") and size == 3
    assert newest_file(tmp_path / "yoq") is None
