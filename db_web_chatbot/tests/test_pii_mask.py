# tests/test_pii_mask.py
# Ishga tushirish: python -m unittest tests.test_pii_mask -v  (repo ildizidan)
import unittest

from app.pii_mask import mask_pii


class MaskPiiTests(unittest.TestCase):
    def test_passport_is_masked(self):
        result = mask_pii("Mening pasportim AA1234567")
        self.assertNotIn("AA1234567", result)
        self.assertIn("[PASPORT OLIB TASHLANDI]", result)

    def test_jshshir_is_masked(self):
        result = mask_pii("JSHSHIR: 12345678901234")
        self.assertNotIn("12345678901234", result)
        self.assertIn("[JSHSHIR OLIB TASHLANDI]", result)

    def test_card_number_plain_is_masked(self):
        result = mask_pii("Kartam 1234567890123456")
        self.assertNotIn("1234567890123456", result)
        self.assertIn("[KARTA RAQAMI OLIB TASHLANDI]", result)

    def test_card_number_with_spaces_is_masked(self):
        result = mask_pii("Kartam 1234 5678 9012 3456 shu")
        self.assertNotIn("1234 5678 9012 3456", result)
        self.assertIn("[KARTA RAQAMI OLIB TASHLANDI]", result)

    def test_card_number_with_dashes_is_masked(self):
        result = mask_pii("1234-5678-9012-3456")
        self.assertIn("[KARTA RAQAMI OLIB TASHLANDI]", result)

    def test_multiple_pii_in_one_message(self):
        result = mask_pii("Pasportim AA1234567, JSHSHIR 12345678901234")
        self.assertNotIn("AA1234567", result)
        self.assertNotIn("12345678901234", result)

    def test_normal_message_is_unchanged(self):
        text = "Sement narxi qancha turadi?"
        self.assertEqual(mask_pii(text), text)

    def test_empty_string_is_unchanged(self):
        self.assertEqual(mask_pii(""), "")

    def test_short_number_is_not_masked(self):
        text = "5 ta kerak, narxi 58000 so'm"
        self.assertEqual(mask_pii(text), text)


if __name__ == "__main__":
    unittest.main()
