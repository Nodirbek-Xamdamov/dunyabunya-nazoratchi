"""Pictures/GIFs the bot saved (media_files + MEDIA_DIR) show up in the group feed; databases without
the table (demo, older backups) still work."""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from PIL import Image

from tests.conftest import use_database
from tests.test_views import assert_clean, run_page

_MEDIA_TABLE = """CREATE TABLE IF NOT EXISTS media_files (message_ref INTEGER NOT NULL, status VARCHAR(12) NOT NULL, path VARCHAR(255),
    size INTEGER, local_date DATE NOT NULL, decided_at DATETIME NOT NULL, PRIMARY KEY (message_ref))"""


@pytest.fixture
def media_db(demo_db, tmp_path):
    """A copy of the demo database with a media_files table: one saved photo and one skipped photo."""
    path = tmp_path / "media_test.db"
    shutil.copy(demo_db, path)  # the demo copies the real database's schema: the table may be there already
    media_dir = tmp_path / "media"
    (media_dir / "2026-10").mkdir(parents=True)
    Image.new("RGB", (1600, 900), (200, 60, 40)).save(media_dir / "2026-10" / "saved.jpg", "JPEG")
    conn = sqlite3.connect(path)
    conn.execute(_MEDIA_TABLE)
    chat_id = conn.execute("""SELECT chat_id FROM messages WHERE media_type = 'photo'
                              GROUP BY chat_id ORDER BY MAX(sent_at_utc) DESC LIMIT 1""").fetchone()[0]
    saved, skipped = [row[0] for row in conn.execute(
        "SELECT id FROM messages WHERE chat_id = ? AND media_type = 'photo' ORDER BY sent_at_utc DESC LIMIT 2",
        (chat_id,))]
    conn.execute("INSERT INTO media_files VALUES (?, 'saved', '2026-10/saved.jpg', 1, '2026-10-01', '2026-10-01')",
                 (saved,))
    conn.execute("INSERT INTO media_files VALUES (?, 'skipped', NULL, NULL, '2026-10-01', '2026-10-01')", (skipped,))
    conn.commit()
    conn.close()
    os.environ["DASHBOARD_MEDIA_DIR"] = str(media_dir)
    use_database(True, path)
    from ui import data

    data.clear()
    yield {"path": path, "dir": media_dir, "chat_id": chat_id, "saved": saved, "skipped": skipped}
    os.environ.pop("DASHBOARD_MEDIA_DIR", None)
    data.clear()


def test_feed_query_has_media_columns_with_and_without_the_table(media_db, demo_db, tmp_path):
    from core import db, queries
    from core.settings import load_settings

    with db.session(load_settings()) as s:
        feed = queries.group_feed(s, media_db["chat_id"], "2000-01-01", "2100-01-01", 5000)
    rows = feed.set_index("id")
    assert rows.loc[media_db["saved"], "media_status"] == "saved"
    assert rows.loc[media_db["saved"], "media_path"] == "2026-10/saved.jpg"
    assert rows.loc[media_db["skipped"], "media_status"] == "skipped"

    older = tmp_path / "older.db"  # a database from before the bot saved pictures
    shutil.copy(demo_db, older)
    conn = sqlite3.connect(older)
    conn.execute("DROP TABLE IF EXISTS media_files")
    conn.close()
    use_database(True, older)
    with db.session(load_settings()) as s:
        feed = queries.group_feed(s, media_db["chat_id"], "2000-01-01", "2100-01-01", 50)
    assert {"media_status", "media_path"} <= set(feed.columns) and feed["media_status"].isna().all()


def test_group_feed_shows_the_saved_picture_and_labels_the_rest(media_db):
    app = run_page("groups", {"g": str(media_db["chat_id"]), "p": "last30"})
    assert_clean(app)
    feed = next(item.proto.body for item in app.get("html") if 'class="db-feed"' in item.proto.body)
    assert feed.count("data:image/jpeg;base64,") == 1  # only the saved one; the skipped one is "[rasm]"
    assert "[rasm]" in feed


def test_media_html_reads_only_saved_files_inside_the_folder(tmp_path):
    from ui import media

    Image.new("RGB", (2000, 1000), (10, 20, 30)).save(tmp_path / "a.jpg", "JPEG")
    (tmp_path / "g.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (tmp_path.parent / "outside.jpg").write_bytes((tmp_path / "a.jpg").read_bytes())

    photo = media.html("photo", "saved", "a.jpg", tmp_path, "rasm")
    assert photo.startswith('<details class="db-msg-media"><summary title="rasm"><img src="data:image/jpeg;base64,')
    gif = media.html("animation", "saved", "g.mp4", tmp_path, "GIF")
    assert '<video src="data:video/mp4;base64,' in gif and "autoplay loop muted playsinline" in gif
    assert media.html("photo", "skipped", "a.jpg", tmp_path) == ""
    assert media.html("photo", "limit", None, tmp_path) == ""
    assert media.html("photo", "saved", "missing.jpg", tmp_path) == ""
    assert media.html("photo", "saved", "../outside.jpg", tmp_path) == ""  # never outside MEDIA_DIR
    (tmp_path / "big.mp4").write_bytes(b"0" * (media.MAX_GIF_BYTES + 1))
    assert media.html("animation", "saved", "big.mp4", tmp_path) == ""


def test_scaled_picture_is_small(tmp_path):
    import base64

    from ui import media

    Image.effect_noise((3000, 2000), 60).convert("RGB").save(tmp_path / "big.jpg", "JPEG", quality=95)
    uri = media.html("photo", "saved", "big.jpg", tmp_path).split('src="', 1)[1].split('"', 1)[0]
    data = base64.b64decode(uri.split(",", 1)[1])
    with Image.open(__import__("io").BytesIO(data)) as image:
        assert max(image.size) == media.PHOTO_SIDE
    assert len(data) < (Path(tmp_path / "big.jpg").stat().st_size)


def test_stylesheet_survives_the_html_sanitizer():
    """st.html's sanitizer drops a whole style block whose text has a "less than" sign followed by a letter
    or slash (2026-10-22: a comment mentioning the details tag switched off the dashboard's whole design)."""
    import re

    from tests.conftest import DASHBOARD_DIR

    css = (DASHBOARD_DIR / "ui" / "style.css").read_text(encoding="utf-8")
    assert not re.search(r"<[/\w]", css)
