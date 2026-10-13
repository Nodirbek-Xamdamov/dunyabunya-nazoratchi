from app.telegram.tg_format import split_message, to_telegram_html


def test_bold_markdown_becomes_html():
    assert to_telegram_html("Hozir **18** ta ochiq vazifa") == "Hozir <b>18</b> ta ochiq vazifa"


def test_heading_and_bullets():
    out = to_telegram_html("### Xulosa\n- birinchi\n* ikkinchi")
    assert out == "<b>Xulosa</b>\n• birinchi\n• ikkinchi"


def test_stray_lt_amp_are_escaped():
    out = to_telegram_html("Summa < 5 mln & qarz > 0")
    assert out == "Summa &lt; 5 mln &amp; qarz &gt; 0"


def test_model_html_tags_kept():
    src = 'Hisobot: <b>tayyor</b> <a href="https://x.com/r?a=1">ochish</a>'
    assert to_telegram_html(src) == src


def test_existing_entities_not_double_escaped():
    assert to_telegram_html("A &amp; B") == "A &amp; B"


def test_markdown_link():
    assert to_telegram_html("[ochish](https://x.com/a)") == '<a href="https://x.com/a">ochish</a>'


def test_narrow_table_becomes_pre():
    src = "| Bo'lim | Soni |\n|---|---|\n| Ombor | 5 |\n| IT | 12 |"
    out = to_telegram_html(src)
    assert out.startswith("<pre>") and out.endswith("</pre>")
    assert "|" not in out and "Ombor" in out and "12" in out


def test_wide_table_becomes_list():
    src = ("| # | Vazifa | Loyiha | Muddat |\n|---|--------|--------|--------|\n"
           "| 1 | Exchange app da clientlardan pul olib berishni qilish kerak | TASK IT | 09.04.2026 |")
    out = to_telegram_html(src)
    assert "|" not in out
    assert out.startswith("1. <b>Exchange app")
    assert "Loyiha: TASK IT" in out and "Muddat: 09.04.2026" in out


def test_code_content_untouched():
    out = to_telegram_html("`a**b**<c>`")
    assert out == "<code>a**b**&lt;c&gt;</code>"


def test_italic_not_triggered_by_math():
    assert to_telegram_html("2 * 3 * 4") == "2 * 3 * 4"


def test_underscore_in_names_untouched():
    assert to_telegram_html("sale_order_line jadvali") == "sale_order_line jadvali"


def test_split_short_is_single():
    assert split_message("salom") == ["salom"]


def test_split_long_respects_limit_and_lines():
    text = "\n".join(f"qator {i} " + "x" * 50 for i in range(200))
    parts = split_message(text, limit=1000)
    assert len(parts) > 1
    assert all(len(p) <= 1000 for p in parts)
    assert "\n".join(parts) == text


def test_split_reopens_pre():
    text = "<pre>" + "\n".join("y" * 30 for _ in range(100)) + "</pre>"
    parts = split_message(text, limit=500)
    for p in parts:
        assert p.count("<pre>") == p.count("</pre>")
