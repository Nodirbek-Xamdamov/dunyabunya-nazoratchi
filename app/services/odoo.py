from __future__ import annotations

import asyncio
import logging
import threading
import time
import xmlrpc.client
from typing import Any

from app.config import settings
from app.name_utils import strip_uzbek_suffixes

log = logging.getLogger(__name__)

# Fuzzy ism solishtirish: RapidFuzz (tez) — mavjud bo'lmasa difflib fallback
_SIMILARITY_THRESHOLD = 75.0   # score >= 75 bo'lgan nomzodlar qabul qilinadi
_CLEAR_LEAD_GAP = 10.0         # yetakchi ikkinchidan shuncha ko'p bo'lsa — yagona nomzod
_LOOSE_SIMILARITY_FLOOR = 40.0  # ro'yxatdan o'tishda "o'xshash nomlar" taklifi uchun (pastroq chegara)

try:
    from rapidfuzz import fuzz as _rapidfuzz_fuzz
    log.info("RapidFuzz similarity enabled")
except Exception:  # noqa: BLE001
    _rapidfuzz_fuzz = None

# Faol res.users ro'yxati keshi (TTL 10 daqiqa) — har qidiruvda Odoo'ni urmaymiz
_users_cache: tuple[float, list] | None = None
_USERS_CACHE_TTL_SECONDS = 600

# Faol hr.employee ro'yxati keshi — alohida (res.users'dan farqli, ancha kattaroq ro'yxat)
_employees_cache: tuple[float, list] | None = None

# Bosqich (project.task.type) ID keshi — (project_id, stage_name) -> stage_id.
# Muddatsiz: bosqichlar amalda deyarli o'zgarmaydi (jami 8 ta bo'lim loyihasi bor),
# shuning uchun har vazifa yaratish/yakunlashda QAYTA-QAYTA search_read yubormaslik
# uchun bir marta topilgach butun jarayon davomida eslab qolinadi.
_stage_cache: dict[tuple[int, str], int] = {}

# project.task'ning state/kanban_state maydonlari SXEMASI (fields_get) — bu Odoo
# modelining o'zi, vazifa/loyihaga bog'liq EMAS, shuning uchun butun jarayon
# davomida faqat BIR MARTA so'raladi (har "Bajarildi/Bekor qilindi" bosilganda emas).
_task_state_meta_cache: dict | None = None

# Odoo'ga parallel so'rovlar chegarasi — server'ni urib yubormaslik uchun.
# Har update alohida task bo'lgani uchun (parallel polling), XML-RPC chaqiruvlari
# ham bir vaqtda ko'payishi mumkin — semaphore buni cheklaydi.
_ODOO_MAX_CONCURRENCY = 8


class OdooError(Exception):
    pass


class _TimeoutTransport(xmlrpc.client.Transport):
    """xmlrpc.client hech qachon o'zidan timeout qo'ymaydi (ulanish osilib qolsa
    abadiy kutadi) — shuning uchun har bir ulanishga qo'lda socket timeout beramiz."""

    def __init__(self, timeout: float, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class _TimeoutSafeTransport(xmlrpc.client.SafeTransport):
    """Xuddi shu, https:// manzillar uchun (Odoo Cloud har doim https)."""

    def __init__(self, timeout: float, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class OdooClient:
    """Oddiy XML-RPC klient."""

    def __init__(
        self,
        url: str = settings.ODOO_URL,
        db: str = settings.ODOO_DB,
        login: str = settings.ODOO_LOGIN,
        api_key: str = settings.ODOO_API_KEY,
        timeout: int = settings.ODOO_TIMEOUT_SECONDS,
    ):
        self.url = url.rstrip("/")
        self.db = db
        self.login = login
        self.api_key = api_key
        self.timeout = timeout
        self._local = threading.local()
        # Barcha execute_kw chaqiruvlari orqali o'tadigan semaphore
        self._semaphore = asyncio.Semaphore(_ODOO_MAX_CONCURRENCY)

    def _make_transport(self):
        # Timeout'ni haqiqiy socket ulanishiga o'rnatish uchun maxsus Transport kerak —
        # ServerProxy(..., timeout=...) degan parametr yo'q, shuning uchun oddiy
        # ServerProxy(url) bilan Odoo tomon javob bermay qolsa so'rov ABADIY osilib
        # qolardi (bu "yillik hisobot hech narsa qaytarmayapti" xatoligining sababi edi).
        if self.url.startswith("https://"):
            return _TimeoutSafeTransport(self.timeout)
        return _TimeoutTransport(self.timeout)

    def _common_proxy(self) -> xmlrpc.client.ServerProxy:
        proxy = getattr(self._local, "common_proxy", None)
        if proxy is None:
            proxy = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", transport=self._make_transport())
            self._local.common_proxy = proxy
        return proxy

    def _object_proxy(self) -> xmlrpc.client.ServerProxy:
        proxy = getattr(self._local, "object_proxy", None)
        if proxy is None:
            proxy = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", transport=self._make_transport())
            self._local.object_proxy = proxy
        return proxy

    def _get_uid(self) -> int:
        # UID thread-local — parallel to_thread chaqiruvlarida race bo'lmasligi uchun
        uid = getattr(self._local, "uid", None)
        if uid is None:
            try:
                uid = self._common_proxy().authenticate(self.db, self.login, self.api_key, {})
            except (TimeoutError, OSError) as exc:
                raise OdooError(f"Odoo {self.timeout}s ichida javob bermadi (timeout, authenticate): {exc}") from exc
            if not uid:
                raise OdooError("Odoo authenticate() muvaffaqiyatsiz — login yoki API key noto'g'ri")
            self._local.uid = uid
        return uid

    def _execute_kw_sync(self, model: str, method: str, args: list, kwargs: dict | None = None) -> Any:
        kwargs = kwargs or {}
        uid = self._get_uid()
        try:
            return self._object_proxy().execute_kw(self.db, uid, self.api_key, model, method, args, kwargs)
        except (TimeoutError, OSError) as exc:
            # Socket timeout — qayta urinish yordam bermaydi (Odoo javob bermayapti),
            # darhol aniq xato bilan chiqamiz (aks holda chaqiruvchi abadiy kutib qolardi)
            raise OdooError(f"Odoo {self.timeout}s ichida javob bermadi (timeout): {exc}") from exc
        except (xmlrpc.client.Fault, xmlrpc.client.ProtocolError):
            # Autentifikatsiya eskirgan bo'lishi mumkin — qayta kirib urinamiz
            self._local.uid = None
            uid = self._get_uid()
            try:
                return self._object_proxy().execute_kw(self.db, uid, self.api_key, model, method, args, kwargs)
            except (TimeoutError, OSError) as exc2:
                raise OdooError(f"Odoo {self.timeout}s ichida javob bermadi (timeout): {exc2}") from exc2
            except xmlrpc.client.Fault as exc2:
                raise OdooError(f"Odoo XML-RPC xato: {exc2}") from exc2

    async def execute_kw(self, model: str, method: str, args: list, kwargs: dict | None = None) -> Any:
        async with self._semaphore:
            return await asyncio.to_thread(self._execute_kw_sync, model, method, args, kwargs)

    @staticmethod
    def _normalize_name(s: str) -> str:
        """Ismni qidiruvga tayyorlash: kichik harf, tinish belgilar va "o'g'li" olib
        tashlanadi. Qo'shimchalarni olib tashlash har bir so'zga alohida
        qo'llanadi (find_users_by_name ichida), bu yerda butun satr tozalanadi."""
        s = s.lower()
        for ch in ["\u2018", "\u2019", "\u0027", "\u0060"]:
            s = s.replace(ch, "")
        s = s.replace("o g li", "").replace("ogli", "").replace("o'g'li", "")
        return s.strip()

    @staticmethod
    def _similarity_score(a: str, b: str) -> float:
        """Ism o'xshashligi (0-100). token_set_ratio + partial_ratio maksimumi.

        token_set_ratio: so'z tartibi/kelishik farqlariga chidamli ("Sardorbekka" bilan
        "Sardorbek" yuqori ball oladi); partial_ratio: qisman moslikni ham topadi.
        """
        if _rapidfuzz_fuzz is not None:
            return max(
                _rapidfuzz_fuzz.token_set_ratio(a, b),
                _rapidfuzz_fuzz.partial_ratio(a, b),
            )
        # difflib fallback — partial_ratio o'rnini bosadigan qisman moslik
        from difflib import SequenceMatcher
        sm = SequenceMatcher(None, a, b)
        best_block = max((m.size for m in sm.get_matching_blocks()), default=0)
        partial = (best_block / len(a) * 100.0) if a else 0.0
        return max(sm.ratio() * 100.0, partial)

    @staticmethod
    def _word_matches(query_word: str, odoo_word: str) -> bool:
        q = query_word.lower()
        o = odoo_word.lower()

        if len(q) < 2 or len(o) < 2:
            return False

        if q in o or o in q:
            return True

        consecutive = 0
        for a, b in zip(q, o):
            if a == b:
                consecutive += 1
            else:
                break

        if len(q) <= 4:
            if consecutive >= len(q):
                return True
        else:
            if consecutive >= 3:
                return True

        return False

    def _dedupe_users(self, users: list[dict]) -> list[dict]:
        """Takroriy xodimlarni olib tashlash — tugmalarda dublikat chiqmasligi uchun.

        Avval id (user_id/id) bo'yicha, keyin normallashtirilgan to'liq ism bo'yicha
        unikal qilinadi. Bir xil odam bir nechta res.users yozuviga ega bo'lsa
        (masalan ichki + portal hisob) ham bitta tugma bo'lib chiqadi.
        """
        seen_ids: set = set()
        seen_names: set = set()
        unique: list[dict] = []
        for u in users:
            uid = u.get("user_id") or u.get("id")
            if uid is not None:
                if uid in seen_ids:
                    continue
                seen_ids.add(uid)
            name_key = self._normalize_name(str(u.get("name") or ""))
            if name_key:
                if name_key in seen_names:
                    continue
                seen_names.add(name_key)
            unique.append(u)
        return unique

    async def _get_active_users(self) -> list[dict]:
        """Faol res.users ro'yxati — xotirada keshlanadi (10 daqiqa), har qidiruvda
        Odoo'ga qayta so'rov yuborilmaydi."""
        global _users_cache
        now = time.time()
        if _users_cache and now - _users_cache[0] < _USERS_CACHE_TTL_SECONDS:
            return _users_cache[1]
        users = await self.execute_kw(
            "res.users", "search_read",
            [[["active", "=", True]], ["id", "name"]],
            {"limit": 500},
        )
        _users_cache = (now, users)
        return users

    async def _get_active_employees(self) -> list[dict]:
        """Faol hr.employee ro'yxati — xotirada keshlanadi (10 daqiqa).

        res.users'dan FARQLI: bu kompaniyaning BUTUN HODIMLAR RO'YXATI (login
        hisobi bor-yo'qligidan qat'iy nazar). Jonli tekshiruvda aniqlandi: 180 ta
        faol hr.employee'dan atigi 54 tasi res.users (login) hisobiga ega — qolgan
        ~126 tasi faqat shu yerda topiladi."""
        global _employees_cache
        now = time.time()
        if _employees_cache and now - _employees_cache[0] < _USERS_CACHE_TTL_SECONDS:
            return _employees_cache[1]
        employees = await self.execute_kw(
            "hr.employee", "search_read",
            [[["active", "=", True]], ["id", "name"]],
            {"limit": 1000},
        )
        _employees_cache = (now, employees)
        return employees

    def _fuzzy_match_candidates(self, name_query: str, candidates: list[dict],
                                 threshold: float = _SIMILARITY_THRESHOLD,
                                 limit: int | None = None) -> list[dict]:
        """Berilgan nomzodlar ro'yxatini (har biri kamida `id`+`name`) ism bo'yicha
        fuzzy solishtiradi. `find_users_by_name`/`find_employees_by_name` uchun
        umumiy yadro — model (res.users/hr.employee) farqi faqat CHAQIRUVCHIDA.

        - RapidFuzz token_set_ratio/partial_ratio (so'z tartibi, kelishik va
          erkalash qo'shimchalariga chidamli);
        - Faqat score >= `threshold` bo'lgan nomzodlar qaytadi, ball kamayish
          tartibida (sukut — qattiq _SIMILARITY_THRESHOLD, lekin chaqiruvchi
          pastroq chegara bilan "o'xshash nomlar" ro'yxati uchun ham ishlatishi
          mumkin — `find_employees_by_name_loose`ga qarang);
        - `limit` berilsa, natija shunga qadar cheklanadi (eng yuqori balldan);
        - Dublikatlar (id va normallashtirilgan ism bo'yicha) olib tashlanadi.
        """
        if not name_query or not name_query.strip():
            return []

        query_norm = self._normalize_name(name_query)
        query_norm = " ".join(
            w for w in (strip_uzbek_suffixes(x) for x in query_norm.split()) if len(w) >= 2
        )
        if not query_norm.strip():
            return []

        matched: list[dict] = []
        for u in candidates:
            uname_norm = self._normalize_name(str(u.get("name") or ""))
            uname_norm = " ".join(
                w for w in (strip_uzbek_suffixes(x) for x in uname_norm.split()) if len(w) >= 2
            )
            if not uname_norm:
                continue
            score = self._similarity_score(query_norm, uname_norm)
            if score >= threshold:
                matched.append({**u, "id": u["id"], "name": u["name"], "score": round(score, 1)})

        matched.sort(key=lambda c: c["score"], reverse=True)
        # Dublikatlarni olib tashlaymiz — bir xil xodim ikki marta chiqmasligi uchun
        deduped = self._dedupe_users(matched)
        return deduped[:limit] if limit is not None else deduped

    async def find_users_by_name(self, name_query: str) -> list[dict]:
        """Ismni Odoo LOGIN hisobi (res.users) bo'yicha fuzzy qidirish — faqat
        shu yerda topilgan xodimga Odoo'da vazifa biriktirish (project.task.user_ids)
        mumkin, chunki bu maydon faqat res.users yozuvlarini qabul qiladi."""
        all_users = await self._get_active_users()
        return self._fuzzy_match_candidates(name_query, all_users)

    async def find_employees_by_name(self, name_query: str) -> list[dict]:
        """Ismni BUTUN HODIMLAR RO'YXATI (hr.employee) bo'yicha fuzzy qidirish.

        find_users_by_name'dan farqli — bu yerda Odoo LOGIN hisobi bo'lmagan
        xodimlar ham topiladi. /start orqali ro'yxatdan o'tishda ishlatiladi:
        login hisobi yo'q xodim ham botga ulanib, o'z profilini ko'ra olishi
        uchun (garchi hozircha Odoo'da unga to'g'ridan-to'g'ri vazifa
        biriktirib bo'lmasa-da — bu Odoo'ning o'z modeli cheklovi, bot xatosi emas).
        """
        all_employees = await self._get_active_employees()
        return self._fuzzy_match_candidates(name_query, all_employees)

    async def find_employees_by_name_loose(self, name_query: str, limit: int = 5) -> list[dict]:
        """`find_employees_by_name` qat'iy chegara (score>=75) bilan HECH KIMNI
        topmasa ishlatiladigan zaxira: xuddi shu hodimlar ro'yxati ustida,
        lekin ANCHA PASTROQ chegara (_LOOSE_SIMILARITY_FLOOR) bilan eng yaqin
        `limit` ta nomzodni qaytaradi — ro'yxatdan o'tishda foydalanuvchi to'liq
        F.I.SH. yozganda-yu, aniq moslik topilmaganda "bunlardan biri sizmi?"
        deb taklif qilish uchun (aks holda foydalanuvchi "topilmadi" xabari bilan
        hech qanday chiqish yo'lisiz qolib ketardi)."""
        all_employees = await self._get_active_employees()
        return self._fuzzy_match_candidates(
            name_query, all_employees, threshold=_LOOSE_SIMILARITY_FLOOR, limit=limit,
        )

    async def get_all_employees(self) -> list[dict]:
        """Barcha faol xodimlar ro'yxatini olish (hr.employee yoki res.users)."""
        try:
            employees = await self.execute_kw(
                "hr.employee", "search_read",
                [[["active", "=", True]]],
                {"fields": ["id", "name", "job_id", "department_id", "work_email", "work_phone"], "limit": 1000},
            )
            if employees:
                results = []
                for e in employees:
                    job = e.get("job_id")
                    job_name = job[1] if isinstance(job, (list, tuple)) and len(job) > 1 else "-"
                    dept = e.get("department_id")
                    dept_name = dept[1] if isinstance(dept, (list, tuple)) and len(dept) > 1 else "-"
                    results.append({
                        "id": e["id"],
                        "name": e.get("name") or "Noma'lum",
                        "job": job_name,
                        "department": dept_name,
                        "email": e.get("work_email") or "-",
                        "phone": e.get("work_phone") or "-",
                    })
                return results
        except Exception as exc:
            log.warning("hr.employee o'qishda xatolik: %s. res.users dan olinmoqda.", exc)

        try:
            users = await self.execute_kw(
                "res.users", "search_read",
                [[["active", "=", True], ["share", "=", False]]],
                {"fields": ["id", "name", "login", "email"], "limit": 1000},
            )
            return [
                {
                    "id": u["id"],
                    "name": u.get("name") or "Noma'lum",
                    "job": "-",
                    "department": "-",
                    "email": u.get("email") or u.get("login") or "-",
                    "phone": "-",
                }
                for u in users
            ]
        except Exception as exc2:
            log.error("res.users o'qishda xatolik: %s", exc2)
            return []

    async def _ensure_stage(self, project_id: int, stage_name: str, *, sequence: int) -> int | None:
        """Loyihada berilgan nomdagi bosqich borligini kafolatlaydi va id'sini qaytaradi
        — natija (project_id, stage_name) bo'yicha keshlanadi (bosqichlar amalda
        deyarli o'zgarmaydi, shuning uchun har chaqiriqda Odoo'ga search_read
        yubormaslik vazifa yaratish/yakunlashni SEZILARLI tezlashtiradi).

        Bo'lim proyektlari (33-40) BOSQICHSIZ (0 ta stage) boshlanadi. Agar shu holatda
        birinchi vazifa "Bajarildi" tugmasi bosilib yakunlansa, o'sha loyiha uchun
        BIRINCHI va shu tariqa YAGONA bosqichni ("Bajarildi") yaratib qo'yardi — Odoo
        esa keyingi barcha yangi vazifalarni ANIQ stage_id berilmasa ENG BIRINCHI (eng
        kichik sequence) bosqichga qo'yadi. Natijada hali hech kim tegmagan yangi
        vazifalar ham "Bajarildi" ustunida ko'rinib qolardi (jonli ma'lumotda topilgan
        va tasdiqlangan haqiqiy xato). Shu sabab: `create_task` har doim avval neytral
        "Jarayonda" (sequence=0) bosqichini kafolatlaydi — "Bajarildi"/"Bekor qilindi"
        (sequence=10) esa undan doim KEYIN turadi."""
        cache_key = (project_id, stage_name)
        cached = _stage_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            existing = await self.execute_kw(
                "project.task.type", "search_read",
                [[["name", "=", stage_name], ["project_ids", "in", [project_id]]], ["id"]],
            )
            if existing:
                stage_id = existing[0]["id"]
            else:
                stage_id = await self.execute_kw(
                    "project.task.type", "create",
                    [{"name": stage_name, "project_ids": [(4, project_id)], "sequence": sequence}],
                )
            _stage_cache[cache_key] = stage_id
            return stage_id
        except Exception as exc:
            # Bosqich kafolatlanmasa ham vazifa yaratilishi TO'XTAMASLIGI kerak —
            # eng yomon holatda Odoo o'zining standart bosqich tanlash mantig'iga qaytadi.
            log.warning("_ensure_stage(project=%s, name=%r) muvaffaqiyatsiz: %s", project_id, stage_name, exc)
            return None

    async def _ensure_working_stage(self, project_id: int) -> int | None:
        return await self._ensure_stage(project_id, "Jarayonda", sequence=0)

    async def create_task(
        self,
        *,
        project_id: int,
        name: str,
        description: str = "",
        user_ids: list[int] | None = None,
        date_deadline: str | None = None,
    ) -> int:
        stage_id = await self._ensure_working_stage(project_id)
        vals: dict[str, Any] = {"project_id": project_id, "name": name}
        if stage_id:
            vals["stage_id"] = stage_id
        if description:
            vals["description"] = description
        if user_ids:
            vals["user_ids"] = [(6, 0, user_ids)]
        if date_deadline:
            vals["date_deadline"] = date_deadline
        return await self.execute_kw("project.task", "create", [vals])

    async def get_task(self, task_id: int) -> dict | None:
        rows = await self.execute_kw("project.task", "read", [[task_id], ["id", "name", "project_id"]])
        return rows[0] if rows else None

    async def delete_task(self, task_id: int) -> None:
        await self.execute_kw("project.task", "unlink", [[task_id]])

    async def _task_state_value(self, meta: dict | None, done: bool) -> str | None:
        """Odoo versiyasiga mos 'state' qiymatini topadi (fields_get selection'idan).

        Odoo 17/18: '1_done'/'1_canceled' kabi, Odoo 16: '04_done'/'05_canceled' kabi
        bo'lishi mumkin — versiyaga bog'liq bo'lmaslik uchun label bo'yicha qidiramiz.
        """
        want = ("Done", "Bajarildi", "Bajarilgan", "Tugallandi") if done else \
               ("Cancelled", "Canceled", "Bekor qilindi", "Bekor qilingan")
        selection = ((meta or {}).get("state") or {}).get("selection") or []
        wanted = {w.casefold() for w in want}
        # Aniq moslik
        for value, label in selection:
            if str(label).strip().casefold() in wanted:
                return value
        # Qisman moslik (masalan "Canceled" vs "Cancelled" farqlari uchun)
        for value, label in selection:
            lab = str(label).strip().casefold()
            if any(lab == w or lab.startswith(w) or w in lab for w in wanted):
                return value
        return None

    async def _get_task_state_meta(self) -> dict:
        """project.task'ning state/kanban_state maydonlari sxemasi — Odoo modelining
        o'zi, hech qanday vazifa/loyihaga bog'liq emas, shuning uchun butun jarayon
        davomida BIR MARTA so'raladi va keshlanadi (har "Bajarildi" bosilganda emas)."""
        global _task_state_meta_cache
        if _task_state_meta_cache is not None:
            return _task_state_meta_cache
        try:
            meta = await self.execute_kw(
                "project.task", "fields_get",
                [["state", "kanban_state"]], {"attributes": ["selection"]},
            )
        except Exception:
            log.warning("fields_get ishlamadi — faqat stage_id yoziladi")
            meta = {}
        _task_state_meta_cache = meta
        return meta

    async def update_task_stage(self, task_id: int, *, done: bool) -> None:
        """Vazifani to'liq yakunlaydi/bekor qiladi:

        - `stage_id`: "Bajarildi"/"Bekor qilindi" bosqichiga o'tkazadi;
        - `state`: Odoo'dagi vazifa holatini (Done/Cancelled) BIRGA yozadi — qiymat
          Odoo versiyasidan (fields_get selection) avtomatik aniqlanadi
          (Odoo 17/18 da '1_done', Odoo 16 da '04_done');
        - `kanban_state`: 'done' — FAQAT maydon mavjud bo'lsa (Odoo 16 va undan
          oldingilari; Odoo 17/18 da bu maydon yo'q).
        """
        # task va meta bir-biriga bog'liq EMAS — PARALLEL so'raladi (foydalanuvchi
        # "Bajarildi" bosgandan keyingi kutish vaqtini qisqartiradi).
        task, meta = await asyncio.gather(self.get_task(task_id), self._get_task_state_meta())
        if task is None:
            raise OdooError(f"#{task_id} vazifa topilmadi")
        project_id = task["project_id"][0]
        stage_name = "Bajarildi" if done else "Bekor qilindi"

        # sequence=10 — har doim "Jarayonda" (sequence=0, _ensure_working_stage) dan
        # KEYIN turishi uchun, aks holda bu yangi bosqich loyihaning yagona/birinchi
        # bosqichiga aylanib, hali tegilmagan tasklar ham shu yerga tushib qolardi.
        stage_id = await self._ensure_stage(project_id, stage_name, sequence=10)
        if stage_id is None:
            raise OdooError(f"'{stage_name}' bosqichini yaratib/topib bo'lmadi")

        has_kanban_state = bool(meta and "kanban_state" in meta)
        state_value = await self._task_state_value(meta, done=done)

        # Yoziladigan maydonlar: stage + state + (agar mavjud bo'lsa) kanban_state
        vals: dict = {"stage_id": stage_id}
        if state_value:
            vals["state"] = state_value
        if done and has_kanban_state:
            vals["kanban_state"] = "done"

        try:
            await self.execute_kw("project.task", "write", [[task_id], vals])
        except Exception as e:
            # state maydoni yozilmaydigan bo'lsa (eski Odoo) — kamida stage+kanban saqlanadi
            log.warning("task %s: to'liq write xato (%s) — base vals bilan qayta urinamiz",
                        task_id, str(e)[:120])
            base_vals: dict = {"stage_id": stage_id}
            if done and has_kanban_state:
                base_vals["kanban_state"] = "done"
            await self.execute_kw("project.task", "write", [[task_id], base_vals])
