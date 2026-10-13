"""app.utils ichidagi sof funksiyalarni sinovdan o'tkazadi (tarmoq/bazaga bog'liq emas)."""

import pytest

from app.utils import clean_text_for_odoo, normalize_username, normalize_uz_phone


class TestNormalizeUsername:
    def test_strips_at_sign(self):
        assert normalize_username("@Someone") == "someone"

    def test_strips_whitespace_and_lowercases(self):
        assert normalize_username("  MyUser  ") == "myuser"

    def test_raises_on_empty_string(self):
        with pytest.raises(ValueError):
            normalize_username("")

    def test_raises_on_only_at_sign(self):
        with pytest.raises(ValueError):
            normalize_username("@")

    def test_raises_on_none(self):
        with pytest.raises(ValueError):
            normalize_username(None)


class TestCleanTextForOdoo:
    def test_empty_input_returns_empty_string(self):
        assert clean_text_for_odoo(None) == ""
        assert clean_text_for_odoo("") == ""

    def test_strips_html_tags(self):
        result = clean_text_for_odoo("<p>Salom</p><br/>Dunyo")
        assert "<" not in result
        assert "Salom" in result and "Dunyo" in result

    def test_removes_slash_and_asterisk(self):
        result = clean_text_for_odoo("Bu/matn *bilan* /belgilar bor")
        assert "/" not in result
        assert "*" not in result

    def test_joins_paragraphs_with_blank_line(self):
        result = clean_text_for_odoo("Birinchi qator\nIkkinchi qator")
        assert result == "Birinchi qator\n\nIkkinchi qator"

    def test_manager_name_gets_single_line_break_after_hurmat_bilan(self):
        raw = "Xabar matni\n\nHurmat bilan,\n\nManager Ismi"
        result = clean_text_for_odoo(raw)
        assert "Hurmat bilan,\nManager Ismi" in result
        assert "Hurmat bilan,\n\nManager Ismi" not in result

    def test_collapses_excess_horizontal_whitespace(self):
        result = clean_text_for_odoo("So'z1     so'z2")
        assert "     " not in result


class TestNormalizeUzPhone:
    """2026-08-06, mijoz aniq so'ragan: ko'rsatiladigan telefon raqamlari uzluksiz
    (ajratkichsiz) va +998 kod bilan bo'lishi kerak."""

    def test_returns_none_for_empty_input(self):
        assert normalize_uz_phone(None) is None
        assert normalize_uz_phone("") is None
        assert normalize_uz_phone(False) is None

    def test_strips_spaces_and_dashes(self):
        assert normalize_uz_phone("+998 90 123-45-67") == "+998901234567"

    def test_adds_998_prefix_to_bare_9_digit_number(self):
        assert normalize_uz_phone("931234567") == "+998931234567"

    def test_does_not_duplicate_998_prefix(self):
        assert normalize_uz_phone("+998931234567") == "+998931234567"
        assert normalize_uz_phone("998931234567") == "+998931234567"

    def test_leaves_non_digit_garbage_as_cleaned_only(self):
        """Raqamsiz axlat qiymat ("q" kabi) -- to'qib chiqarilmaydi, bo'sh qaytadi."""
        assert normalize_uz_phone("q") is None

    def test_leaves_unrecognized_length_untouched_beyond_cleaning(self):
        """998 bilan boshlanmaydigan va 9 xonali ham bo'lmagan raqam -- taxmin
        qilinmaydi, faqat ajratkichlardan tozalanadi."""
        assert normalize_uz_phone("51651651") == "51651651"
