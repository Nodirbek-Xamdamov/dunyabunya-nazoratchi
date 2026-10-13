import unittest
from datetime import date

from app.dates import Reply, parse_date, parse_reply

TODAY = date(2026, 10, 5)
DUE = date(2026, 10, 12)


class ParseDateTest(unittest.TestCase):
    def test_dates(self):
        cases = {
            "15.10.2026": date(2026, 10, 15),
            "15.10.26": date(2026, 10, 15),
            "15/10/2026": date(2026, 10, 15),
            "15-10-2026": date(2026, 10, 15),
            "2026-10-15": date(2026, 10, 15),
            " 5.11 ": date(2026, 11, 5),
            "15.10.2026 y.": date(2026, 10, 15),
            "15.10.26г": date(2026, 10, 15),
            "15.10 gacha": date(2026, 10, 15),
            "15.10.": date(2026, 10, 15),
            "15,10,2026": date(2026, 10, 15),
            "15, 10, 2026 +": date(2026, 10, 15),
            ", 15,10 +": date(2026, 10, 15),
            "15 oktabr": date(2026, 10, 15),
            "15-oktabr": date(2026, 10, 15),
            "15 Oktyabr 2026": date(2026, 10, 15),
            "15 oktabrgacha": date(2026, 10, 15),
            "15 oktabr 2026 yil": date(2026, 10, 15),
            "15 октября": date(2026, 10, 15),
            "3 ноября 2026 г.": date(2026, 11, 3),
            "20 may": date(2027, 5, 20),
            "bugun": TODAY,
            "ertaga": date(2026, 10, 6),
            "Завтра": date(2026, 10, 6),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_date(text, TODAY), expected)

    def test_no_year_means_next_occurrence(self):
        self.assertEqual(parse_date("05.10", TODAY), TODAY)
        self.assertEqual(parse_date("04.10", TODAY), date(2027, 10, 4))
        self.assertEqual(parse_date("05.01", date(2026, 12, 20)), date(2027, 1, 5))

    def test_explicit_past_year_is_kept(self):
        self.assertEqual(parse_date("01.09.2026", TODAY), date(2026, 9, 1))

    def test_not_a_date(self):
        for text in ["rahmat", "ok", "15", "2 marta", "45.50", "12-70", "15.10 to'laydi dedi", "tel 90 123 45 67",
                     "15.10.2026 va 20.10.2026", "x" * 100, ""]:
            with self.subTest(text=text):
                self.assertIsNone(parse_date(text, TODAY))

    def test_nonexistent_day_is_an_error(self):
        for text in ["31.11.2026", "31 noyabr", "29.02.2027"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_date(text, TODAY)


class ParseReplyTest(unittest.TestCase):
    def test_name_written_with_a_hash(self):
        cases = {
            "#Nodir, 12,10,2026 +": Reply(DUE, "Nodir"),
            "#Nodir,12,10,2026+": Reply(DUE, "Nodir"),
            "#Nodir 12.10.2026": Reply(DUE, "Nodir"),
            "12,10,2026 #Nodir +": Reply(DUE, "Nodir"),
            "#Nodir\n12,10,2026\n+": Reply(DUE, "Nodir"),
            "#Нодир, 12,10 +": Reply(DUE, "Нодир"),
            "#Nodir #Ali 12/10/26": Reply(DUE, "Nodir, Ali"),
            "#Nodir ertaga +": Reply(date(2026, 10, 6), "Nodir"),
            "#Nodir 12 oktabr +": Reply(DUE, "Nodir"),
            # with a hash the date may stand among other words
            "#Nodir, 12,10,2026 + to'laydi dedi": Reply(DUE, "Nodir"),
            "#Nodir 5,5 mln beradi 12,10,2026": Reply(DUE, "Nodir"),
            "#Nodir 12 oktabrda to'laydi": Reply(DUE, "Nodir"),
            "#Nodir 112,10,2026": Reply(None, "Nodir"),
            "#Nodir +": Reply(None, "Nodir"),
            "#Nodir gaplashdi, hali aniq emas": Reply(None, "Nodir"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_reply(text, TODAY), expected)

    def test_name_written_without_a_hash(self):
        cases = {
            "SAIDAHMAD \n12.10.2026\n+": Reply(DUE, "SAIDAHMAD"),
            "Nodir, 12,10,2026 +": Reply(DUE, "Nodir"),
            "Nodir 12.10": Reply(DUE, "Nodir"),
            "Nodir aka 12 oktabr +": Reply(DUE, "Nodir aka"),
            "G'ayrat: 12/10/2026": Reply(DUE, "G'ayrat"),
            "Нодир 12,10,2026": Reply(DUE, "Нодир"),
            "Nodir ertaga": Reply(date(2026, 10, 6), "Nodir"),
            # a date alone has no name: the bot then shows who wrote the reply
            "12,10,2026 +": Reply(DUE, None),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_reply(text, TODAY), expected)

    def test_ordinary_chat_is_not_a_payment_date(self):
        for text in ["rahmat", "+", "nma boldi", "12,10,2026 + to'laydi dedi", "15.10 to'laydi dedi", "5,5 mln",
                     "Nodir gaplashdi", "tel 90 123 45 67", "bu juda uzun gap ekan 12.10.2026", "Nodir2 12.10.2026"]:
            with self.subTest(text=text):
                self.assertIsNone(parse_reply(text, TODAY))

    def test_nonexistent_day_is_an_error(self):
        for text in ["#Nodir, 31,11,2026 +", "#Nodir 31,11,2026 to'laydi", "Nodir 31.11.2026"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_reply(text, TODAY)


if __name__ == "__main__":
    unittest.main()
