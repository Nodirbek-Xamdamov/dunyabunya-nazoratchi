# tests/test_catalog.py
# Ishga tushirish: python -m unittest tests.test_catalog -v  (repo ildizidan)
import unittest

from app import catalog


def _item(id, name, category, price=100000.0, in_stock=True, brand=None, name_uz=None):
    return {
        "id": id,
        "name": name,
        "nameUz": name_uz,
        "price": price,
        "inStock": in_stock,
        "category": category,
        "brand": brand,
    }


PIPES = "Hammasi / Muhandislik tizimlari / Трубы и фитинги"
CEMENT = "Hammasi / Qurilish mahsulotlari / Цемент и смеси"

ITEMS = [
    _item(40961, "Adapter PPR VERO (Ø75mm-2*1/2, ichki)", PIPES, 248880.0, False, "VERO"),
    _item(40962, "Mufta PPR VERO (Ø20mm)", PIPES, 5000.0, False, "VERO"),
    _item(40963, "Mufta PPR KALDE (Ø20mm)", PIPES, 6000.0, True, "KALDE"),
    _item(50001, "Sement M400 Bekobod", CEMENT, 58000.0, True),
    _item(50002, "Fuga 902 Eco VENTUM (120-Qora)", CEMENT, 0.0, False, "VENTUM"),
    _item(60001, "Drel mikser  PHM120-C PIT", "Hammasi / Instrumentlar", 840000.0, True, name_uz="Drel-mikser PHM120-C PIT"),
]


class SearchProductsTests(unittest.TestCase):
    def setUp(self):
        catalog._set_products(ITEMS)

    def test_out_of_stock_product_is_found_with_price_and_link(self):
        result = catalog.search_products("Adapter PPR VERO")
        self.assertEqual(result[0]["name"], "Adapter PPR VERO (Ø75mm-2*1/2, ichki)")
        self.assertEqual(result[0]["price"], 248880.0)
        self.assertFalse(result[0]["in_stock"])
        self.assertEqual(result[0]["link"], "https://demo.dunyabunya.uz/uz/catalog/40961")
        self.assertNotIn("id", result[0])

    def test_word_order_and_case_do_not_matter(self):
        result = catalog.search_products("vero adapter")
        self.assertEqual(result[0]["name"], "Adapter PPR VERO (Ø75mm-2*1/2, ichki)")

    def test_alternatives_from_same_category_in_stock_first(self):
        names = [p["name"] for p in catalog.search_products("Adapter")]
        self.assertEqual(
            names,
            ["Adapter PPR VERO (Ø75mm-2*1/2, ichki)", "Mufta PPR KALDE (Ø20mm)", "Mufta PPR VERO (Ø20mm)"],
        )

    def test_alias_falls_back_to_category(self):
        # "siment" hech bir nomda yo'q, alias orqali "цемент" kategoriyasiga tushadi.
        names = [p["name"] for p in catalog.search_products("siment")]
        self.assertEqual(names, ["Sement M400 Bekobod", "Fuga 902 Eco VENTUM (120-Qora)"])

    def test_zero_price_is_returned_as_unknown(self):
        result = catalog.search_products("Fuga 902")
        self.assertIsNone(result[0]["price"])

    def test_name_uz_and_russian_link(self):
        uz = catalog.search_products("PHM120-C", "uz")[0]
        ru = catalog.search_products("PHM120-C", "ru")[0]
        self.assertEqual(uz["name"], "Drel-mikser PHM120-C PIT")
        self.assertEqual(ru["name"], "Drel mikser PHM120-C PIT")
        self.assertEqual(ru["link"], "https://demo.dunyabunya.uz/ru/catalog/60001")

    def test_unknown_lang_defaults_to_uz(self):
        result = catalog.search_products("PHM120-C", "en")[0]
        self.assertEqual(result["link"], "https://demo.dunyabunya.uz/uz/catalog/60001")

    def test_root_category_prefix_is_stripped(self):
        result = catalog.search_products("Adapter PPR VERO")[0]
        self.assertEqual(result["category"], "Muhandislik tizimlari / Трубы и фитинги")

    def test_nothing_found(self):
        self.assertEqual(catalog.search_products("olma"), [])
        self.assertEqual(catalog.search_products("   "), [])


if __name__ == "__main__":
    unittest.main()
