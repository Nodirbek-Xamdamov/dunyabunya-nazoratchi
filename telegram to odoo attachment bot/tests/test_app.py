import pytest

from docbot.app import InstanceLock
from docbot.telegram.handlers import accepts_document


def test_only_one_instance_can_hold_the_lock(tmp_path):
    first, second = InstanceLock(tmp_path / "docbot.lock"), InstanceLock(tmp_path / "docbot.lock")
    assert first.acquire()
    assert (tmp_path / "docbot.pid").read_text(encoding="ascii").isdigit()
    assert not second.acquire()
    first.release()
    assert not (tmp_path / "docbot.pid").exists()
    assert second.acquire()
    second.release()


def test_pictures_and_pdfs_are_accepted_other_files_are_not():
    assert accepts_document("image/png", "scan.png")
    assert accepts_document("image/jpeg", None)
    assert accepts_document("application/pdf", "S82411.pdf")
    assert accepts_document("application/octet-stream", "IMG_0001.HEIC")  # some phones send no image type
    assert accepts_document(None, "scan.jpg")
    assert not accepts_document("application/zip", "docs.zip")
    assert not accepts_document(None, None)


@pytest.mark.parametrize(
    ("mime", "name"),
    [
        ("video/mp4", "clip.mp4"),
        ("video/quicktime", "IMG_0002.MOV"),
        ("image/gif", "funny.gif"),  # an animation
        ("video/mp4", "scan.jpg"),  # the declared type wins over a misleading name
        ("application/octet-stream", "clip.mp4"),
        (None, "clip.mov"),
    ],
)
def test_videos_and_animations_are_never_taken(mime, name):
    assert not accepts_document(mime, name)
