"""Synthetic message templates for the demo database (construction materials topics, uz/ru).
Every template is (uzbek text, russian text, one-sentence uzbek summary). No real customer data."""

MATERIALS = [
    ("armatura 12 lik", "арматура 12"), ("armatura 14 lik", "арматура 14"), ("sement M400", "цемент М400"),
    ("sement M500", "цемент М500"), ("kafel 60x60", "кафель 60х60"), ("gipsokarton Knauf", "гипсокартон Knauf"),
    ("PPR truba 20 lik", "труба ППР 20"), ("profil 60x27", "профиль 60х27"), ("g'isht", "кирпич"),
    ("penoplast 5 sm", "пенопласт 5 см"), ("fasad bo'yog'i", "фасадная краска"), ("shpaklyovka", "шпаклёвка"),
    ("laminat 33 klass", "ламинат 33 класс"), ("metallocherepitsa", "металлочерепица"),
    ("kanalizatsiya trubasi 110", "канализационная труба 110"), ("plitka yelimi", "плиточный клей"),
]
DISTRICTS = ["Chilonzor", "Yunusobod", "Sergeli", "Yakkasaroy", "Mirzo Ulug'bek", "Olmazor", "Bektemir", "Uchtepa",
             "Shayxontohur", "Yashnobod", "Mirobod", "Yangihayot", "Zangiota", "Qibray", "Chirchiq", "Nurafshon"]

# category -> sentiment -> templates; {m} material, {d} district, {q} quantity, {n} small number
TEMPLATES = {
    "Narx va To'lovlar": {
        "Neytral": [
            ("{m} narxi qancha bo'ldi?", "Сколько сейчас стоит {mr}?", "Mijoz {m} narxini so'radi"),
            ("{q} ta {m} uchun schet chiqarib bering", "Выставьте счёт на {q} шт {mr}", "Mijoz {m} uchun hisob-faktura so'radi"),
            ("To'lov qildim, tekshirib ko'ring", "Оплатил, проверьте пожалуйста", "Mijoz to'lov qilganini aytib tekshirishni so'radi"),
            ("Akt sverka yuborib bera olasizmi?", "Пришлите акт сверки", "Mijoz akt sverka so'radi"),
            ("Ulgurji narx bo'ladimi {m} ga?", "Есть оптовая цена на {mr}?", "Mijoz {m} uchun ulgurji narx so'radi"),
        ],
        "Shikoyat": [
            ("Nega schetda narx kelishganimizdan qimmat?", "Почему в счёте цена выше договорённой?", "Mijoz hisob-fakturadagi narx kelishilganidan yuqoriligidan norozi"),
            ("To'lov qilganimga 3 kun bo'ldi, hali ko'rinmayapti", "Оплатил три дня назад, оплаты не видно", "Mijoz to'lovi ko'rinmayotganidan shikoyat qildi"),
        ],
    },
    "Dastavka va Logistika": {
        "Neytral": [
            ("Buyurtmam qachon yetib keladi?", "Когда будет доставка?", "Mijoz buyurtma qachon yetkazilishini so'radi"),
            ("Ertaga {d}ga yetkazib bera olasizmi?", "Можно доставить завтра в {d}?", "Mijoz ertaga {d}ga yetkazib berishni so'radi"),
            ("Mashina soat nechada chiqadi?", "Во сколько выедет машина?", "Mijoz mashina jo'nash vaqtini so'radi"),
            ("Yetkazib berish narxi qancha {d}gacha?", "Сколько стоит доставка до {d}?", "Mijoz {d}gacha yetkazish narxini so'radi"),
        ],
        "Shikoyat": [
            ("{n} kundan beri kutyapman, mashina kelmadi", "Жду уже {n} дня, машины нет", "Mijoz yetkazib berish kechikkanidan norozi"),
            ("Haydovchi {n} soat kechikdi, ishchilar bekor turibdi", "Водитель опоздал на {n} часа", "Mijoz haydovchi kechikkanidan shikoyat qildi"),
            ("Yuk boshqa manzilga borib qolibdi", "Груз увезли не на тот адрес", "Mijoz yuk noto'g'ri manzilga ketganidan shikoyat qildi"),
        ],
    },
    "Mahsulot Sifati va Nuqsonlar": {
        "Neytral": [
            ("{m} qaysi zavodniki?", "{mr} какого производителя?", "Mijoz {m} ishlab chiqaruvchisini so'radi"),
            ("{m} ga sertifikat bormi?", "Есть сертификат на {mr}?", "Mijoz {m} sertifikatini so'radi"),
        ],
        "Shikoyat": [
            ("{m} singan holda keldi", "{mr} пришла битая", "Mijoz {m} singan kelganidan shikoyat qildi"),
            ("Sementingiz qotib qolgan ekan", "Цемент затвердел в мешках", "Mijoz sement qotib qolganidan shikoyat qildi"),
            ("{m} sifati juda past chiqdi", "Качество {mr} очень плохое", "Mijoz {m} sifati pastligidan norozi"),
        ],
    },
    "Komplektatsiya va Hajm": {
        "Neytral": [
            ("{m} ni yana {q} taga ko'paytirsak bo'ladimi?", "Можно добавить ещё {q} шт {mr}?", "Mijoz buyurtmaga {m} qo'shishni so'radi"),
        ],
        "Shikoyat": [
            ("{q} ta buyurtma qilgandim, {n} tasi kam keldi", "Заказывал {q}, не хватает {n} шт", "Mijoz {m} kam kelganidan shikoyat qildi"),
            ("Boshqa o'lchamdagi {m} olib kelishibdi", "Привезли не тот размер {mr}", "Mijoz boshqa o'lchamdagi tovar kelganidan norozi"),
        ],
    },
    "Xodimlar va Xizmat Ko'rsatish": {
        "Neytral": [
            ("Bizning menejerimiz kim bo'ladi endi?", "Кто теперь наш менеджер?", "Mijoz mas'ul menejer kimligini so'radi"),
        ],
        "Shikoyat": [
            ("Menejer ikki kundan beri telefonni ko'tarmayapti", "Менеджер второй день не отвечает", "Mijoz menejer javob bermayotganidan shikoyat qildi"),
            ("Haydovchingiz qo'pol gapirdi", "Ваш водитель нагрубил", "Mijoz haydovchining muomalasidan norozi"),
            ("Va'da qilgan edingiz, hech kim qayta qo'ng'iroq qilmadi", "Обещали перезвонить и пропали", "Mijoz va'da bajarilmaganidan shikoyat qildi"),
        ],
    },
    "Qaytarish va Kafolat": {
        "Neytral": [
            ("Ortib qolgan {m} ni qaytarib olasizmi?", "Можно вернуть лишний {mr}?", "Mijoz ortib qolgan {m}ni qaytarishni so'radi"),
            ("{m} ga kafolat bormi?", "Есть гарантия на {mr}?", "Mijoz {m} kafolatini so'radi"),
        ],
        "Shikoyat": [
            ("Brak {m} ni almashtirib bering, bir haftadan beri kutyapman", "Замените бракованный {mr}, жду неделю", "Mijoz brak tovar almashtirilmayotganidan norozi"),
        ],
    },
    "Ijobiy Fikrlar": {
        "Ijobiy": [
            ("Rahmat, yetib keldi, hammasi joyida", "Спасибо, всё привезли вовремя", "Mijoz tovar o'z vaqtida yetib kelganidan mamnun"),
            ("Tez yetkazdingiz, barakalla", "Быстро доставили, молодцы", "Mijoz tez yetkazib berilganini maqtadi"),
            ("Xizmatingizdan juda mamnunmiz", "Очень довольны вашим сервисом", "Mijoz xizmatdan mamnunligini bildirdi"),
            ("{m} sifati zo'r ekan, yana olamiz", "{mr} отличного качества, будем брать ещё", "Mijoz {m} sifatini maqtadi"),
        ],
    },
    "Boshqa": {
        "Neytral": [
            ("{m} bormi hozir?", "Есть в наличии {mr}?", "Mijoz {m} bor-yo'qligini so'radi"),
            ("{q} ta {m} buyurtma bermoqchiman", "Хочу заказать {q} шт {mr}", "Mijoz {m} buyurtma bermoqchi"),
            ("Shanba kuni ishlaysizlarmi?", "Вы работаете в субботу?", "Mijoz shanba kuni ish vaqtini so'radi"),
            ("Omboringiz qayerda joylashgan?", "Где находится ваш склад?", "Mijoz ombor manzilini so'radi"),
            ("{m} o'rniga boshqa variant bormi?", "Есть замена для {mr}?", "Mijoz {m} o'rniga muqobil variant so'radi"),
        ],
    },
}

CATEGORY_WEIGHTS = {
    "Boshqa": 30, "Narx va To'lovlar": 20, "Dastavka va Logistika": 19, "Ijobiy Fikrlar": 9,
    "Komplektatsiya va Hajm": 7, "Mahsulot Sifati va Nuqsonlar": 7, "Xodimlar va Xizmat Ko'rsatish": 4,
    "Qaytarish va Kafolat": 4,
}
COMPLAINT_PROB = {
    "Dastavka va Logistika": 0.35, "Mahsulot Sifati va Nuqsonlar": 0.75, "Komplektatsiya va Hajm": 0.7,
    "Xodimlar va Xizmat Ko'rsatish": 0.8, "Narx va To'lovlar": 0.12, "Qaytarish va Kafolat": 0.45,
    "Boshqa": 0.0, "Ijobiy Fikrlar": 0.0,
}

# (uz, ru, summary, classified by rule?)
FILTERED = [
    ("Assalomu alaykum", "Здравствуйте", "Mijoz salomlashdi", False),
    ("Ok", "Ок", "Mijoz tasdiqladi", False),
    ("Xo'p, tushunarli", "Понятно", "Mijoz tushunganini bildirdi", False),
    ("Rahmat", "Спасибо", "Mijoz rahmat aytdi", False),
    ("+", "+", None, True),
    ("👍", "👍", None, True),
]
STAFF_REPLIES = [
    ("Assalomu alaykum, {m} bor, hozir narxini yuboraman", "Здравствуйте, {mr} есть, сейчас скину цену"),
    ("Ertaga ertalab yetkazib beramiz", "Завтра утром доставим"),
    ("Hozir aniqlab aytaman", "Сейчас уточню"),
    ("Schetni yubordim, tekshiring", "Счёт отправил, проверьте"),
    ("Uzr, kechikish uchun. Bugun albatta yetkazamiz", "Извините за задержку, сегодня точно доставим"),
    ("To'lov tushdi, rahmat", "Оплата поступила, спасибо"),
    ("Brak tovarni almashtiramiz, ombordan chiqarishyapti", "Брак заменим, уже отгружают"),
]
PEER_REPLIES = [
    ("Ha, bor ekan, men ham kecha oldim", "Да, есть, я вчера брал", "Mijoz boshqa mijozga tovar borligini aytdi"),
    ("Ertalab mashina chiqqan, hozir yo'lda", "Машина утром выехала, уже в пути", "Mijoz mashina yo'lda ekanini aytdi"),
]

UZ_FIRST = ["Akmal", "Dilshod", "Jasur", "Bobur", "Sardor", "Aziz", "Rustam", "Sherzod", "Otabek", "Bekzod", "Javlon",
            "Nodir", "Farrux", "Shoxrux", "Doniyor", "Islom", "Anvar", "Murod", "Kamol", "Nilufar", "Dilnoza",
            "Madina", "Gulnora", "Zarina", "Sevara", "Feruza", "Malika", "Temur", "Asror", "Lochin"]
RU_FIRST = ["Алексей", "Сергей", "Дмитрий", "Ольга", "Ирина", "Андрей", "Наталья", "Виктор", "Елена", "Игорь"]
SURNAMES = ["Karimov", "Rahimov", "Tursunov", "Aliyev", "Yusupov", "Saidov", "Ergashev", "Nazarov", "Qodirov",
            "Xolmatov", "Ismoilov", "Abdullayev", "Mirzayev", "Sobirov", "Hamidov", "Umarov"]
OBJECTS = ["uy", "hovli", "ko'p qavatli", "ofis", "do'kon", "maktab", "kafe", "ombor", "dacha", "turar-joy", "klinika"]
COMPANY_ACCOUNTS = [
    ("dunyabunya Santexnika", "dunyabunya_santex"), ("dunyabunya Ombor", "dunyabunya_ombor"),
    ("dunyabunya Logistika", "dunyabunya_logist"), ("dunyabunya 035 9999", "dunyabunya_manager"),
]
