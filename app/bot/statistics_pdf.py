"""Mijozning buyurtmalar statistikasini (app.odoo_client.get_monthly_statistics
qaytargan `{"months": [...], "by_object": [...]}`dan -- joriy yilning to'liq
Yanvar->Dekabr oralig'i, hali kelmagan oylar ham 0 bilan) grafik (sahifa 1) +
oylik jadval bilan bitta PDF hujjatiga yig'adi.

Matplotlib bilan birga keladigan DejaVu Sans shriftidan foydalanamiz (fpdf2'ning
o'rnatilgan asosiy shriftlari -- Helvetica/Arial -- kirill harflarini QO'LLAMAYDI, rus
tilidagi matn chiqib qolishi mumkin) -- alohida shrift fayli yoki apt-get paketi shart
emas, matplotlib allaqachon requirements.txt'da bor va o'zining mpl-data/fonts/ttf/
DejaVuSans.ttf faylini har doim o'zi bilan olib yuradi."""

import logging
from datetime import datetime, timedelta, timezone
from io import BytesIO

import matplotlib

matplotlib.use("Agg")  # GUI backend kerak emas -- konteynerda display yo'q
import matplotlib.pyplot as plt
from fpdf import FPDF
from matplotlib.ticker import FuncFormatter

from app.i18n import t

logger = logging.getLogger("aiogram_bot")

TASHKENT_TZ = timezone(timedelta(hours=5))

MONTH_NAMES = {
    "uz": [
        "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
        "Iyul", "Avgust", "Sentyabr", "Oktyabr", "Noyabr", "Dekabr",
    ],
    "ru": [
        "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
        "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
    ],
}

# 2026-08-05, mijoz bergan namuna (Excel) rasmiga mos ranglar: ko'k=buyurtma,
# to'q sariq=qaytarilgan, kulrang=to'langan (grafik chiziqlari HAM, jadval
# sarlavha/"Jami" qatorlari esa qora fon + oq matn, qator fonlari oq/och ko'k
# navbatma-navbat -- pastdagi TABLE_* konstantalarga qarang).
CHART_COLOR_SPENT = "#4472C4"
CHART_COLOR_RETURNED = "#ED7D31"
CHART_COLOR_PAID = "#A5A5A5"

TABLE_HEADER_FILL = (0, 0, 0)
TABLE_HEADER_TEXT = (255, 255, 255)
TABLE_ROW_FILL_EVEN = (255, 255, 255)
TABLE_ROW_FILL_ODD = (221, 235, 247)
TABLE_ROW_TEXT = (0, 0, 0)
TABLE_TOTAL_FILL = (0, 0, 0)
TABLE_TOTAL_TEXT = (255, 255, 255)


def _dejavu_font_path() -> str:
    """matplotlib bilan birga o'rnatiladigan DejaVuSans.ttf yo'lini qaytaradi -- kirill,
    lotin va boshqa keng qamrovli belgilarni o'z ichiga oladi, fpdf2'ning o'rnatilgan
    asosiy shriftlaridan farqli."""
    from matplotlib import get_data_path

    return f"{get_data_path()}/fonts/ttf/DejaVuSans.ttf"


def _month_name(month_key: str, lang: str) -> str:
    """'YYYY-MM' -> 'Yanvar' (yoki 'Январь') -- yil ko'rsatilmaydi, chunki yil PDF
    sarlavhasida ko'rsatiladi (jadvalda oy nomining o'zi yetarli)."""
    _, month = month_key.split("-")
    names = MONTH_NAMES.get(lang, MONTH_NAMES["uz"])
    return names[int(month) - 1]


def _fmt(value: float) -> str:
    """Sonni minglik ajratkichi bilan formatlaydi -- QIYMAT 0 BO'LSA BO'SH QATOR
    qaytaradi (jadvallarda "0" umuman ko'rinmasin, faqat haqiqiy nolmas summalar
    ko'rsatilsin).

    Minglik ajratkichi VERGUL emas, BO'SHLIQ ("22 410 500"). Kasr qismi (tiyin)
    FAQAT haqiqatan nolmas bo'lsa ko'rsatiladi -- Odoo ba'zan butun bo'lmagan summa
    qaytarishi mumkin (masalan 5000000.13), bu holda ".13" saqlanadi
    ("5 000 000.13"); lekin 5000000.00 kabi butun summalar uchun ortiqcha ".00"
    YOZILMAYDI ("5 000 000")."""
    if not value:
        return ""
    formatted = f"{value:,.2f}"
    if formatted.endswith(".00"):
        formatted = formatted[:-3]
    return formatted.replace(",", " ")


def _build_chart_image(months: list[dict], lang: str, year: str) -> bytes:
    """Har oy uchun UCHTA chiziqli (buyurtma/qaytarilgan/to'langan, alohida rangda)
    grafik chizadi va PNG bayt ketma-ketligini qaytaradi (diskka yozmasdan,
    xotirada). Och kulrang fon + oq to'r chiziqlari, yuqori chap burchakda
    legenda -- Y o'qi mijozning eng ko'p xarid qilgan (yoki qaytargan/to'lagan)
    oyidagi summasidan sezilarli YUQORIROQ qilib belgilanadi (`* 1.20` bo'sh joy),
    shu sabab eng baland nuqta ham grafik chekkasiga tegib turmaydi. Y o'qi
    belgilari matplotlib'ning standart siqilgan ilmiy shakli o'rniga to'liq,
    minglik ajratkichli sonlar bilan ko'rsatiladi (`FuncFormatter`).

    Chiziq ranglari (2026-08-05, mijoz bergan Excel namunasiga mos): ko'k=buyurtma,
    to'q sariq=qaytarilgan, kulrang=to'langan -- `CHART_COLOR_*` konstantalariga
    qarang. Fon OQ, to'r esa faqat GORIZONTAL (uzun) chiziqlardan iborat -- vertikal
    chiziqlar yo'q, "kvadrat" to'r emas (2026-08-05, mijoz aniq so'ragan).

    `returned` (odoo_client.py'da DOIM manfiy saqlanadi) shu yerda GRAFIK uchun
    mutlaq (musbat) qiymatga o'tkaziladi; `spent`/`paid` hech narsa o'zgarmagan
    holda chiziladi.

    `subplots_adjust`: grafik jadval bilan bir xil kenglikda ko'rinishi uchun
    qo'lda qattiq chegaralar bilan grafik atrofidagi bo'sh joy minimallashtiriladi
    -- chizilgan maydon deyarli rasmning chap/o'ng chekkalarigacha yetadi."""
    labels = [_month_name(m["month"], lang) for m in months]
    spent = [m["spent"] for m in months]
    returned = [abs(m["returned"]) for m in months]
    paid = [m["paid"] for m in months]

    fig, ax = plt.subplots(figsize=(10, 5.2))
    ax.set_facecolor("white")
    x = list(range(len(labels)))
    ax.plot(x, spent, marker="o", linewidth=2.5, color=CHART_COLOR_SPENT, label=t("statistics_col_spent", lang))
    ax.plot(x, returned, marker="o", linewidth=2.5, color=CHART_COLOR_RETURNED, label=t("statistics_col_returned", lang))
    ax.plot(x, paid, marker="o", linewidth=2.5, color=CHART_COLOR_PAID, label=t("statistics_col_paid", lang))

    max_value = max(spent + returned + paid + [0])
    ax.set_ylim(0, max_value * 1.20 if max_value > 0 else 1)
    # Minglik ajratkichi VERGUL emas, BO'SHLIQ (jadvallardagi `_fmt()` bilan bir
    # xil -- grafik o'qi butun sonlar bilan ishlagani uchun kasr qismi hisobga
    # olinmaydi, faqat ajratkichi mos qilinadi).
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}".replace(",", " ")))

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.tick_params(axis="y", labelsize=8)
    # 2026-08-05, mijoz aniq so'ragan: fon OQ (avval kulrang edi), to'r esa
    # "kvadrat" (gorizontal+vertikal) emas, faqat GORIZONTAL uzun chiziqlardan
    # iborat (`axis="y"` -- vertikal chiziqlar butunlay o'chirilgan).
    ax.grid(axis="y", color="#D9D9D9", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(loc="upper left")
    fig.subplots_adjust(left=0.075, right=0.995, top=0.97, bottom=0.19)

    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=150)
    plt.close(fig)
    return buffer.getvalue()


def build_statistics_pdf(months: list[dict], partner_name: str, lang: str) -> bytes:
    """Generatsiya vaqti (o'ng yuqori burchak) + mijoz ismi (chap tomonda, eng
    tepada) + sarlavha (markazda) + grafik (sahifa 1, uchta chiziq -- buyurtma/
    qaytarilgan/to'langan) + oylik jadval (oy | buyurtma | qaytarilgan | to'langan)
    bilan PDF quradi, xom baytlarni qaytaradi (diskka yozmaydi -- chaqiruvchi
    to'g'ridan-to'g'ri Telegram'ga hujjat sifatida yuboradi).

    Oylik jadvalda: (1) oy nomi yil KO'RSATILMASDAN chiqadi (yil sarlavhada), (2)
    buyurtma VA qaytarilgan ikkalasi ham 0 bo'lgan oylar (masalan hali kelmagan
    kelajakdagi oylar) jadvaldan olib tashlanadi, (3) `returned` JADVALDA ham
    (grafikdagi kabi) mutlaq (musbat) qiymatda ko'rsatiladi, (4) so'm ustunlari
    (Buyurtma/Qaytarilgan/To'langan) O'NGGA tekislangan, "Oy" ustuni esa CHAP
    tomonga (ustun SARLAVHALARI baribir markazda qoladi, faqat qator QIYMATLARI
    chapga siljiydi).

    Jadval ranglari (2026-08-05, mijoz bergan Excel namunasiga mos): sarlavha va
    "Jami" qatori QORA fon + OQ matn; ma'lumot qatorlari OQ/OCH KO'K navbatma-navbat
    (`TABLE_*` konstantalariga qarang).

    QIYMATI 0 GA TENG har qanday katak BO'SH qoldiriladi, "0" umuman yozilmaydi
    (`_fmt()`ga qarang). Sahifaga sig'masa fpdf2 o'zining standart
    `auto_page_break`i bilan avtomatik yangi sahifaga o'tadi.

    Balans ustuni va obyektlar bo'yicha jadval ATAYLAB YO'Q (mijoz aniq so'ragan --
    faqat buyurtma/qaytarilgan/to'langan ko'rsatilsin, mos ravishda grafikka
    "to'langan" uchinchi chiziq sifatida qo'shildi)."""
    year = months[0]["month"].split("-")[0] if months else ""

    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("DejaVu", "", _dejavu_font_path())

    generated_at = datetime.now(TASHKENT_TZ).strftime("%d.%m.%Y %H:%M")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("DejaVu", size=8)
    pdf.cell(0, 5, generated_at, align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("DejaVu", size=14)
    pdf.cell(0, 5, partner_name, new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.ln(5)
    pdf.cell(0, 10, t("statistics_title", lang, year=year), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(4)

    chart_bytes = _build_chart_image(months, lang, year)
    pdf.image(BytesIO(chart_bytes), x=10, w=190)

    pdf.ln(5)
    pdf.set_font("DejaVu", size=12)
    pdf.cell(0, 8, t("quotation_general_label", lang), new_x="LMARGIN", new_y="NEXT")

    col_widths = (40, 50, 50, 50)
    col_aligns = ("L", "R", "R", "R")
    headers = (
        t("statistics_col_month", lang),
        t("statistics_col_spent", lang),
        t("statistics_col_returned", lang),
        t("statistics_col_paid", lang),
    )
    pdf.set_font("DejaVu", size=9)
    pdf.set_fill_color(*TABLE_HEADER_FILL)
    pdf.set_text_color(*TABLE_HEADER_TEXT)
    for header, width in zip(headers, col_widths):
        pdf.cell(width, 8, header, border=1, align="C", fill=True)
    pdf.ln()

    pdf.set_font("DejaVu", size=9)
    pdf.set_text_color(*TABLE_ROW_TEXT)
    total_spent = 0.0
    total_returned = 0.0
    total_paid = 0.0
    row_index = 0
    for month in months:
        total_spent += month["spent"]
        total_returned += month["returned"]
        total_paid += month["paid"]

        if month["spent"] == 0 and month["returned"] == 0:
            continue

        row = (
            _month_name(month["month"], lang),
            _fmt(month["spent"]),
            _fmt(abs(month["returned"])),
            _fmt(month["paid"]),
        )
        pdf.set_fill_color(*(TABLE_ROW_FILL_EVEN if row_index % 2 == 0 else TABLE_ROW_FILL_ODD))
        for value, width, align in zip(row, col_widths, col_aligns):
            pdf.cell(width, 7, value, border=1, align=align, fill=True)
        pdf.ln()
        row_index += 1

    pdf.set_font("DejaVu", size=9)
    pdf.set_fill_color(*TABLE_TOTAL_FILL)
    pdf.set_text_color(*TABLE_TOTAL_TEXT)
    total_row = (
        t("statistics_row_total", lang),
        _fmt(total_spent),
        _fmt(abs(total_returned)),
        _fmt(total_paid),
    )
    for value, width, align in zip(total_row, col_widths, col_aligns):
        pdf.cell(width, 8, value, border=1, align=align, fill=True)

    return bytes(pdf.output())
