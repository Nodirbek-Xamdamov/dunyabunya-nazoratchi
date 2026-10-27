"""W2-04 episode text (voice transcript / photo description in place of the message), offline."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from w2analyst.render import Line, render, render_line  # noqa: E402
from w2analyst.segmenter import TZ  # noqa: E402

T = datetime(2026, 10, 20, 10, 5, tzinfo=TZ)


def line(**kw) -> str:
    return render_line(Line(**{"tg_message_id": 7, "sent_at": T, "sender_name": "Ali", "role": "client"} | kw))


class Render(unittest.TestCase):
    def test_text(self):
        self.assertEqual(line(text="Sement bormi?"), "[10:05 #7] [CLIENT] Ali: Sement bormi?")

    def test_roles(self):
        self.assertIn("[EMPLOYEE] Ali:", line(role="employee", text="x"))
        self.assertIn("[UNKNOWN] Ali:", line(role="unknown", text="x"))
        self.assertIn("[UNKNOWN] ?:", line(role=None, sender_name=None, text="x"))

    def test_time_is_tashkent(self):
        self.assertTrue(line(sent_at=T.astimezone(ZoneInfo("UTC")), text="x").startswith("[10:05 #7]"))  # 05:05 UTC

    def test_voice(self):
        self.assertEqual(line(kind="voice", duration_s=42, transcript="Mashina qayerda?"),
                         "[10:05 #7] [CLIENT] Ali: [voice 0:42] Mashina qayerda?")
        self.assertEqual(line(kind="voice", duration_s=125.4), "[10:05 #7] [CLIENT] Ali: [voice 2:05] (transkript hali yo'q)")
        self.assertEqual(line(kind="video_note", transcript="salom"), "[10:05 #7] [CLIENT] Ali: [video] salom")

    def test_photo(self):
        self.assertEqual(line(kind="photo", description="Siniq kafel"), "[10:05 #7] [CLIENT] Ali: [photo] Siniq kafel")
        self.assertEqual(line(kind="photo", description="Siniq kafel", text="mana"),
                         "[10:05 #7] [CLIENT] Ali: [photo] Siniq kafel | mana")
        self.assertEqual(line(kind="photo", text="mana"), "[10:05 #7] [CLIENT] Ali: [photo] mana")
        self.assertEqual(line(kind="photo"), "[10:05 #7] [CLIENT] Ali: [photo] (tavsif hali yo'q)")

    def test_other_media(self):
        self.assertEqual(line(kind="sticker"), "[10:05 #7] [CLIENT] Ali: [sticker]")
        self.assertEqual(line(kind="document", text="schyot.pdf"), "[10:05 #7] [CLIENT] Ali: [document] schyot.pdf")

    def test_flags(self):
        self.assertEqual(line(text="x", forward_from="Prorab", reply_to=5, deleted=True),
                         "[10:05 #7] [CLIENT] Ali: [forward: Prorab] [reply #5] x (o'chirilgan xabar)")

    def test_purged_text_and_transcript(self):
        self.assertEqual(line(text=None, text_purged=True), "[10:05 #7] [CLIENT] Ali: (matn o'chirilgan)")
        self.assertEqual(line(kind="voice", duration_s=3, transcript=None, text_purged=True),
                         "[10:05 #7] [CLIENT] Ali: [voice 0:03] (matn o'chirilgan)")

    def test_order(self):
        a = Line(tg_message_id=2, sent_at=T, text="b")
        b = Line(tg_message_id=1, sent_at=T, text="a")
        self.assertEqual(render([a, b]).splitlines()[0][-1], "a")


if __name__ == "__main__":
    unittest.main()
