"""
Guruh xabarlarini tasniflovchi modelni (`app/group_ai.py`) belgilangan misollar to'plamida
sinash -- qaysi model (OpenAI yoki lokal Ollama modeli) bot vazifasiga yaraydi, shuni solishtirish
uchun. Bot ishlatadigan AYNAN o'sha prompt, sxema va ishonch chegarasi qo'llanadi.

Ishga tushirish (loyiha papkasidan):

    .venv\\Scripts\\python.exe scripts\\eval_group_ai.py --provider ollama --model gemma4:12b
    .venv\\Scripts\\python.exe scripts\\eval_group_ai.py --provider openai --model gpt-5.4-mini

Har bir misolda faqat belgilangan maydonlar tekshiriladi:
  mijoz xabari -- category (shikoyat/maqtov/hech narsa), stock (tovar yo'q), reply (javob kutadimi),
                  topic (8 ta toifadan qaysi biri: delivery / quality / ... / positive / other)
  xodim xabari -- stock, promise (necha daqiqada javob berishni aytdi; None = va'da yo'q)
"""

import argparse
import asyncio
import logging
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from app import group_ai  # noqa: E402
from app.config import settings  # noqa: E402

M, X = "mijoz", "xodim"

# (kim yozgan, oldingi xabarlar, xabar, kutilgan natija)
CASES: list[tuple[str, str, str, dict]] = [
    # --- Mijozlarning o'zaro oddiy suhbati: hech narsa tashlanmasligi kerak -----------------
    (M, "[mijoz] Sardor: Kecha futbolni ko'rdingmi?", "Ha, juda yomon o'ynashdi, norozi bo'ldim",
     dict(category="none", stock=False, reply=False)),
    (M, "[mijoz] Sardor: Bugun bozorga bordim", "Bozorda sement yo'q ekan, hamma joyda tugabdi",
     dict(category="none", stock=False, reply=False)),
    (M, "[mijoz] Sardor: Ustani topdingmi?", "Topdim, zo'r usta ekan, katta rahmat senga",
     dict(category="none", stock=False, reply=False)),
    (M, "[mijoz] Sardor: Kecha futbolni ko'rdingmi?", "Yo'q, kim yutdi?",
     dict(category="none", stock=False, reply=False)),
    (M, "[mijoz] Sardor: Ustani topdingmi?", "Qayerdan topsa bo'ladi yaxshi ustani?",
     dict(category="none", stock=False, reply=False)),
    (M, "", "Hammaga salom", dict(category="none", stock=False, reply=False)),
    (M, "[mijoz] Sardor: Ertaga to'yga borasanmi?", "Boraman, soat nechida boshlanadi?",
     dict(category="none", stock=False, reply=False)),
    (M, "[mijoz] Bobur: Yangi mashina oldim", "Muborak bo'lsin, zo'r mashina ekan!",
     dict(category="none", stock=False, reply=False)),
    (M, "[mijoz] Bobur: Qo'shnimning ustalari ishni rasvo qilib ketishdi",
     "Ha, hozir yaxshi usta topish qiyin, hammasi aldaydi",
     dict(category="none", stock=False, reply=False)),
    (M, "[mijoz] Сардор: Бугун ҳаво жуда иссиқ", "Ҳа, ишлаб бўлмаяпти, жуда ёмон",
     dict(category="none", stock=False, reply=False)),
    (M, "[mijoz] Олег: Вчера в банке два часа простоял", "Ужас, там всегда так, обслуживание отвратительное",
     dict(category="none", stock=False, reply=False)),
    (M, "[mijoz] Sardor: Mahallada gaz yana o'chdi", "Bizda ham yo'q, uch kundan beri kutyapmiz",
     dict(category="none", stock=False, reply=False)),
    (M, "[mijoz] Bobur: Ertaga yomg'ir yog'arkan", "Unda ertaga beton quymaymiz",
     dict(category="none", stock=False, reply=False)),

    # --- Kompaniyaga oid oddiy yozishma: shikoyat ham, maqtov ham emas -----------------------
    (M, "", "Menga sement kerak, qanchadan?", dict(category="none", stock=False, reply=True, topic="price")),
    (M, "", "Ertaga Chilonzorga yetkazib bera olasizmi?", dict(category="none", stock=False, reply=True, topic="delivery")),
    (M, "", "Armatura 12 lik bormi sizlarda", dict(category="none", stock=False, reply=True, topic="other")),
    (M, "", "Здравствуйте, сколько стоит гипсокартон?", dict(category="none", stock=False, reply=True, topic="price")),
    (M, "[xodim] Ali: Narxi 65 ming", "Ok rahmat", dict(category="none", stock=False, reply=False)),
    (M, "[xodim] Ali: Ertaga 10 da yetkazamiz", "Yaxshi, kutaman", dict(category="none", stock=False, reply=False)),
    (M, "[xodim] Ali: Narxi 65 ming", "Yomon emas, ertaga olaman", dict(category="none", stock=False, reply=False)),
    (M, "", "Brak chiqsa almashtirasizmi?", dict(category="none", stock=False, reply=True, topic="returns")),
    (M, "", "Kechirasiz, ertaga omborga kechikib boraman", dict(category="none", stock=False)),
    (M, "", "Bu tovar yo'q bo'lsa boshqasini bering", dict(category="none", stock=False)),
    (M, "", "Ассалому алайкум, цемент М400 нархи қанча?", dict(category="none", stock=False, reply=True, topic="price")),
    (M, "", "Buyurtma bermoqchiman: 50 qop sement, 2 tonna armatura", dict(category="none", stock=False, reply=True, topic="other")),
    (M, "", "Yetkazib berish pullikmi?", dict(category="none", stock=False, reply=True, topic="delivery")),
    (M, "[xodim] Ali: Buyurtmangiz qabul qilindi", "Rahmat", dict(category="none", stock=False, reply=False)),
    (M, "", "Schet faktura yuborib bera olasizmi?", dict(category="none", stock=False, reply=True, topic="price")),
    (M, "", "Dostavka nechpul chilonzorga", dict(category="none", stock=False, reply=True, topic="delivery")),
    (M, "[xodim] Ali: To'lov qildingizmi?", "Ha, hozir o'tkazdim", dict(category="none", stock=False)),
    (M, "", "Можно завтра к 9 утра привезти на объект?", dict(category="none", stock=False, reply=True, topic="delivery")),
    (M, "", "Sement bormi?", dict(category="none", stock=False, reply=True, topic="other")),

    # --- Shikoyatlar ----------------------------------------------------------------------
    (M, "", "Uch kundan beri kutyapman, hech kim javob bermayapti", dict(category="complaint", reply=True)),
    (M, "[xodim] Ali: Buyurtmangiz yo'lda", "Kecha ham shunday degandingiz, hali kelmadi. Bu qanaqa ish?!",
     dict(category="complaint", reply=True, topic="delivery")),
    (M, "", "Sifati past ekan sementingizni, yarmi qotib qolgan", dict(category="complaint", stock=False, topic="quality")),
    (M, "", "Haydovchingiz juda qo'pol gapirdi, bunaqasi ketmaydi", dict(category="complaint", stock=False, topic="staff")),
    (M, "", "Опять привезли не то, что заказывали. Сколько можно?", dict(category="complaint", stock=False, topic="completeness")),
    (M, "", "Буюртмамни икки марта кечиктирдингиз, энди бошқа жойдан оламан", dict(category="complaint", stock=False, topic="delivery")),
    (M, "", "Narxni 60 ming deb kelishgandik, schetda 65 ming yozilgan. Aldayapsizlarmi?",
     dict(category="complaint", reply=True, topic="price")),
    (M, "", "10 qop buyurtma qilgandim, 8 ta kepti, 2 tasi qani?", dict(category="complaint", reply=True, topic="completeness")),
    (M, "", "sizlani dastavkela umuman ishlamidi, 2 kundan beri kutvomman", dict(category="complaint", stock=False, topic="delivery")),
    (M, "", "Менеджер не берёт трубку второй день, это нормально вообще?", dict(category="complaint", reply=True, topic="staff")),
    (M, "[xodim] Ali: Ertaga aniq yetkazamiz", "Ha albatta, o'tgan hafta ham \"aniq\" degandingiz, zo'r xizmat",
     dict(category="complaint", stock=False)),
    (M, "", "Operatoringiz telefonni ko'tarmayapti, uchinchi marta qo'ng'iroq qilyapman",
     dict(category="complaint", stock=False, topic="staff")),

    # --- Ijobiy fikrlar -------------------------------------------------------------------
    (M, "", "Xizmatingizdan juda xursandman, o'z vaqtida yetkazdingiz", dict(category="praise", stock=False, reply=False, topic="positive")),
    (M, "[xodim] Ali: Yetib bordimi?", "Ha, kechikmasdan olib kelishdi, haydovchiga alohida rahmat, barakalla",
     dict(category="praise", stock=False)),
    (M, "", "Спасибо большое, всё привезли быстро, очень довольны", dict(category="praise", stock=False, reply=False, topic="positive")),
    (M, "", "Раҳмат сизларга, сифати зўр экан, усталар ҳам мақтади", dict(category="praise", stock=False, reply=False, topic="positive")),
    (M, "", "Ali akaga rahmat, hamma narsani tushuntirib berdi, bunaqa xizmatni birinchi ko'rishim",
     dict(category="praise", stock=False)),
    (M, "", "Narxlaringiz ham, sifat ham yaxshi, doim sizlardan olaman", dict(category="praise", stock=False, topic="positive")),

    # --- Tovar yo'q -----------------------------------------------------------------------
    (X, "[mijoz] Vali: M500 sement bormi?", "Afsuski bu tovar hozir yo'q ekan", dict(stock=True, promise=None)),
    (M, "", "Omboringizga bordim, armatura qolmagan deyishdi", dict(stock=True)),
    (X, "[mijoz] Vali: Gipsokarton kerak edi", "Сейчас нет в наличии, на следующей неделе будет",
     dict(stock=True, promise=None)),
    (X, "[mijoz] Vali: M500 sement bormi?", "Bor, 65 mingdan", dict(stock=False, promise=None)),
    (X, "[mijoz] Вали: Арматура 14 борми?", "Ҳозир омборда қолмаган, эртага келади", dict(stock=True, promise=None)),
    (X, "[mijoz] Vali: Shifer bormi?", "Shifer tugagan, profnastil bor", dict(stock=True, promise=None)),
    (X, "[mijoz] Vali: Kafel kerak edi 20 kvadrat", "Kechirasiz, bu rangdagisi tugab qoldi",
     dict(stock=True, promise=None)),
    (X, "[mijoz] Vali: Bugun yetkazasizmi?", "Bugun mashina yo'q, ertaga ertalab yetkazamiz",
     dict(stock=False, promise=None)),
    (M, "[xodim] Ali: Qaysi sement kerak?", "M500, lekin kecha sizlarda yo'q deyishgandi", dict(stock=True)),

    # --- Xodimning "keyinroq javob beraman" va'dasi ------------------------------------------
    (X, "[mijoz] Vali: Narxi qancha?", "10 minutda javob beraman", dict(stock=False, promise=10)),
    (X, "[mijoz] Vali: Qachon keladi?", "Yarim soatda aniqlab aytaman", dict(stock=False, promise=30)),
    (X, "[mijoz] Vali: Qachon keladi?", "Tovar 30 minutda yetib boradi", dict(stock=False, promise=None)),
    (X, "[mijoz] Vali: Narxi qancha?", "Hozir aniqlab aytaman", dict(stock=False, promise=5)),
    (X, "[mijoz] Вали: Есть арматура 12?", "Через час отвечу, уточню на складе", dict(stock=False, promise=60)),
    (X, "[mijoz] Vali: Armatura bormi?", "5 daqiqa kuting, omborga qo'ng'iroq qilyapman", dict(stock=False, promise=5)),
    (X, "[mijoz] Vali: Narxi qancha?", "Narxi 65 ming so'm", dict(stock=False, promise=None)),
    (X, "[mijoz] Вали: Нархи қанча?", "15 дақиқада жавоб бераман", dict(stock=False, promise=15)),
    (X, "[mijoz] Vali: Qachon yetkazasiz?", "Ertaga 10 da yetkazamiz", dict(stock=False, promise=None)),
    (X, "[mijoz] Vali: Schet yuboring", "20 minutdan keyin yozaman", dict(stock=False, promise=20)),
    (X, "[mijoz] Vali: Assalomu alaykum", "Assalomu alaykum, eshitaman", dict(stock=False, promise=None)),
    (X, "[mijoz] Вали: Сколько стоит доставка?", "Минутку, сейчас посмотрю", dict(stock=False, promise=5)),
    (X, "[mijoz] Vali: Chegirma bo'ladimi?", "Bir soatda javobini aytaman", dict(stock=False, promise=60)),
    (X, "[mijoz] Vali: Qachon yetkazasiz?", "2 soatdan keyin yetkazib beramiz", dict(stock=False, promise=None)),
]


# Promptdagi misollarning birortasi bilan ustma-ust tushmaydigan "yangi" misollar -- model
# promptdagi namunalarni yodlab emas, ma'noni tushunib javob berayotganini tekshirish uchun.
HELD_OUT: list[tuple[str, str, str, dict]] = [
    (M, "[mijoz] Jasur: Bugun ishga kech qoldim", "Menam, probka dahshat edi",
     dict(category="none", stock=False, reply=False)),
    (M, "[mijoz] Jasur: Boshqa firmadan g'isht oldim, yarmi singan chiqdi",
     "Ular doim shunaqa, men ham kuyganman ulardan", dict(category="none", stock=False, reply=False)),
    (M, "[mijoz] Jasur: Do'konlarda shakar qolmabdi", "Ha, kecha uchta do'konga kirdim, hech qayerda yo'q",
     dict(category="none", stock=False, reply=False)),
    (M, "[mijoz] Олег: Кто знает хорошего электрика?", "Могу дать номер, отличный мастер, быстро сделал",
     dict(category="none", stock=False, reply=False)),
    (M, "", "Juma muborak hammaga", dict(category="none", stock=False, reply=False)),

    (M, "", "Gazoblok bir donasi necha pul?", dict(category="none", stock=False, reply=True, topic="price")),
    (M, "", "Kartadan to'lasam bo'ladimi", dict(category="none", stock=False, reply=True, topic="price")),
    (M, "", "Эртага соат нечида очасизлар?", dict(category="none", stock=False, reply=True, topic="other")),
    (M, "[xodim] Ali: Manzilni yuboring", "Sergeli 5, 12-uy", dict(category="none", stock=False)),
    (M, "[xodim] Ali: Soat 3 da yetkazamiz", "Bo'ldi, tushunarli", dict(category="none", stock=False, reply=False)),
    (M, "", "Какая минимальная сумма заказа для бесплатной доставки?", dict(category="none", stock=False, reply=True)),
    (M, "", "Profil 60x27 dan 100 ta kerak edi, ertaga olib ketaman", dict(category="none", stock=False, reply=True, topic="other")),

    (M, "", "Kecha olib kelgan g'ishtlaringizni yarmi singan, pulimni qaytaringlar",
     dict(category="complaint", reply=True, topic="quality")),
    (M, "", "Третий раз звоню, обещали перезвонить и тишина. Так дела не делаются",
     dict(category="complaint", stock=False, topic="staff")),
    (M, "", "Юк машинангиз дарвозамни уриб кетди, ким жавоб беради бунга?", dict(category="complaint", reply=True)),
    (M, "", "Narxingiz boshqalardan qimmat ekan, yana dostavkaga ham pul oldingiz",
     dict(category="complaint", stock=False, topic="price")),
    (M, "[xodim] Ali: Uzr, mashina buzilib qoldi", "Har safar bir bahona, endi ishonmayman sizlarga",
     dict(category="complaint", stock=False)),

    (M, "", "Kecha buyurtma berdim, bugun ertalab yetib keldi. Tezkor ishlaysizlar, gap yo'q",
     dict(category="praise", stock=False, reply=False)),
    (M, "", "Ребята, вы лучшие, уже третий объект с вами строю", dict(category="praise", stock=False, reply=False, topic="positive")),
    (M, "", "Menejeringiz Dilshod juda odobli yigit ekan, hammasini tez hal qildi",
     dict(category="praise", stock=False, reply=False, topic="positive")),

    (X, "[mijoz] Vali: Oq sement bormi?", "Oq sement qolmadi, kulrangi bor", dict(stock=True, promise=None)),
    (X, "[mijoz] Вали: Нужен утеплитель 50 мм", "К сожалению, закончился, ждём поставку в четверг",
     dict(stock=True, promise=None)),
    (M, "", "Saytingizda bor deb turibdi, kelsam plitka yo'q deyishdi", dict(stock=True)),
    (X, "[mijoz] Vali: Chegirma bormi?", "Hozircha chegirma yo'q", dict(stock=False, promise=None)),
    (X, "[mijoz] Vali: Armatura 10 bormi?", "Ha, omborda yetarli", dict(stock=False, promise=None)),

    (X, "[mijoz] Vali: Dostavka qancha bo'ladi?", "Bir zumda aniqlab beraman", dict(stock=False, promise=5)),
    (X, "[mijoz] Vali: Schet tayyormi?", "40 minutda xabar beraman", dict(stock=False, promise=40)),
    (X, "[mijoz] Вали: Можно оплатить перечислением?", "Дайте 15 минут, уточню у бухгалтера",
     dict(stock=False, promise=15)),
    (X, "[mijoz] Vali: Mashina chiqdimi?", "Mashina bir soatda chiqadi", dict(stock=False, promise=None)),
    (X, "[mijoz] Vali: Kafel qachon bo'ladi?", "Tovar ertaga keladi", dict(stock=False, promise=None)),
    (X, "[mijoz] Вали: Нархини айтинг", "Ярим соатда ёзаман сизга", dict(stock=False, promise=30)),
    (X, "[mijoz] Vali: Umumiy summa qancha chiqadi?", "Hisoblab ko'rib, 1 soat ichida yozaman",
     dict(stock=False, promise=60)),
]


# 8 ta toifa (topic) uchun qo'shimcha misollar -- promptdagi namunalar bilan ustma-ust tushmaydi.
# Bu yerda asosan `topic` tekshiriladi; shikoyat ekani shubhasiz bo'lgan joyda category ham.
TOPIC_CASES: list[tuple[str, str, str, dict]] = [
    (M, "", "Olgan bo'yog'imning muddati o'tgan ekan, hidi ham boshqacha", dict(category="complaint", topic="quality")),
    (M, "", "Gipsokartonlar namlanib, chetlari uvalanib ketibdi", dict(category="complaint", topic="quality")),
    (M, "", "20 ta gipsokarton so'ragandim, 17 ta tushiribsizlar", dict(category="complaint", topic="completeness")),
    (M, "", "Plitkaning rangi men buyurtma qilgan kulrang emas, jigarrang kelibdi",
     dict(category="complaint", topic="completeness")),
    (M, "", "Вместо арматуры 12 привезли 10, перепутали опять", dict(category="complaint", topic="completeness")),
    (M, "", "Ortib qolgan 5 qop sementni qaytarib olasizlarmi?", dict(category="none", reply=True, topic="returns")),
    (M, "", "Kafelga kafolat bormi, ko'chib ketsa nima bo'ladi?", dict(category="none", reply=True, topic="returns")),
    (M, "", "Можно вернуть лишний утеплитель? Упаковку не вскрывали", dict(category="none", reply=True, topic="returns")),
    (M, "", "Sotuvchingiz bilan gaplashib bo'lmaydi, savol bersam jerkib tashlaydi",
     dict(category="complaint", topic="staff")),
    (M, "", "Ваш кладовщик нахамил и отказался грузить, пока я не поругался", dict(category="complaint", topic="staff")),
    (M, "", "Mashinangiz manzilni topolmay ikki soatdan beri aylanib yuribdi", dict(topic="delivery")),
    (M, "", "Buyurtmam ombordan chiqdimi, soat nechida yetib keladi?", dict(category="none", reply=True, topic="delivery")),
    (M, "", "Kran-manipulyator bilan tushirib bera olasizlarmi?", dict(category="none", reply=True, topic="delivery")),
    (M, "", "100 qop olsam chegirma qilib berasizlarmi?", dict(category="none", reply=True, topic="price")),
    (M, "", "Вчера оплатил по счёту, а у вас оплата до сих пор не видна", dict(reply=True, topic="price")),
    (M, "", "Qarzim qancha qoldi, akt sverka yuborib bering", dict(category="none", reply=True, topic="price")),
    (M, "", "Shanba kuni ishlaysizlarmi?", dict(category="none", reply=True, topic="other")),
    (M, "", "Sizlarda penoplast ham sotiladimi?", dict(category="none", reply=True, topic="other")),
]


def _actual(analysis: group_ai.MessageAnalysis) -> dict:
    return dict(
        category=analysis.category,
        stock=analysis.out_of_stock,
        reply=analysis.needs_reply,
        promise=analysis.promised_minutes,
        topic=analysis.category_key,
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["openai", "ollama"], default=settings.GROUP_AI_PROVIDER)
    parser.add_argument("--model", default=settings.GROUP_AI_MODEL)
    parser.add_argument("--verbose", action="store_true", help="to'g'ri chiqqan misollarni ham ko'rsatish")
    parser.add_argument("--only-new", action="store_true", help="faqat promptda yo'q yangi misollar")
    args = parser.parse_args()

    settings.GROUP_AI_PROVIDER = args.provider
    settings.GROUP_AI_MODEL = args.model
    logging.disable(logging.INFO)

    # Birinchi so'rov modelni xotiraga yuklaydi -- tezlik o'lchoviga kirmaydi
    started = time.monotonic()
    if await group_ai._request_llm("Salom", False) is None:
        sys.exit(f"Model javob bermadi: provider={args.provider} model={args.model}")
    print(f"model={args.model} provider={args.provider} (yuklash: {time.monotonic() - started:.1f} s)\n")

    latencies: list[float] = []
    for title, cases in (
        ("Asosiy to'plam", CASES), ("Yangi misollar (promptda yo'q)", HELD_OUT), ("8 ta toifa", TOPIC_CASES),
    ):
        if args.only_new and cases is CASES:
            continue
        print(f"=== {title}")
        await run_set(cases, args.verbose, latencies)

    ordered = sorted(latencies)
    print(
        f"\nTezlik: o'rtacha {statistics.median(latencies):.2f} s, "
        f"eng sekin 5% {ordered[int(len(ordered) * 0.95) - 1]:.2f} s, jami {sum(latencies):.0f} s"
    )


async def run_set(cases: list[tuple[str, str, str, dict]], verbose: bool, latencies: list[float]) -> None:
    field_total: dict[str, int] = {}
    field_ok: dict[str, int] = {}
    wrong_forward = missed = cases_ok = failed_requests = 0

    for sender, context, text, expected in cases:
        is_staff = sender == X
        t0 = time.monotonic()
        data = await group_ai._request_llm(text, is_staff, context)
        latencies.append(time.monotonic() - t0)
        analysis = group_ai._interpret(data, is_staff, text) if data is not None else None
        if analysis is None:
            failed_requests += 1
            print(f"XATO  so'rov bajarilmadi | {text}")
            continue

        actual = _actual(analysis)
        errors = []
        for field, want in expected.items():
            field_total[field] = field_total.get(field, 0) + 1
            if actual[field] == want:
                field_ok[field] = field_ok.get(field, 0) + 1
                continue
            errors.append(f"{field}: kutilgan={want} chiqdi={actual[field]}")
            # Topikka keraksiz xabar tushishi va kerakli xabarning o'tkazib yuborilishi alohida sanaladi
            if field in ("category", "stock"):
                if want in ("none", False):
                    wrong_forward += 1
                else:
                    missed += 1
        cases_ok += not errors
        if errors or verbose:
            raw = f"about={data.get('about_company')} conf={data.get('confidence')}"
            print(f"{'XATO' if errors else 'OK  '} [{sender}] {text}\n      {'; '.join(errors) or 'ok'} ({raw})")

    fields = "  ".join(f"{f} {field_ok.get(f, 0)}/{field_total[f]}" for f in ("category", "stock", "reply", "promise", "topic") if f in field_total)
    print(f"--> {cases_ok}/{len(cases)} to'liq to'g'ri   ({fields})")
    print(f"    topikka keraksiz tushgan: {wrong_forward}, o'tkazib yuborilgan: {missed}, bajarilmagan so'rov: {failed_requests}\n")


if __name__ == "__main__":
    asyncio.run(main())
