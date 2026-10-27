"""Synthetic demo data for W3 (W0-09 early part). No real messages, people or camera frames.

Deterministic: the same anchor day gives byte-identical SQL. Two business days = anchor-1 and anchor
(Asia/Tashkent, UTC+5, no DST). Ids are not referenced; rows are linked through natural keys.
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta, timezone

TZ = timezone(timedelta(hours=5))
SLA_S = 300

GROUPS = [  # tg_chat_id, username, title
    (-1009000000001, None, "TEST · Chilonzor qurilish do'koni"),
    (-1009000000002, "test_qurilish_demo", "TEST · Sergeli ulgurji savdo"),
    (-1009000000003, None, "TEST · Yunusobod mijozlar"),
]
STAFF = [(900001, "Aziz (menejer)", True), (900002, "Dilnoza (menejer)", True), (900003, "Bekzod (sotuvchi)", False)]
CLIENTS = [(910001, "Mijoz Anvar"), (910002, "Mijoz Gulnora"), (910003, "Mijoz Sardor"), (910004, "Mijoz Malika"),
           (910005, "Mijoz Javlon"), (910006, "Mijoz Shahnoza"), (910007, "Mijoz Rustam"), (910008, "Mijoz Nilufar")]
MANAGER_OF = {0: 900001, 1: 900002, 2: 900001}

# (type, topic, sentiment, severity, client lines, staff lines)
SCENARIOS = [
    ("shikoyat", "Dastavka va Logistika", "negative", 3,
     ["Assalomu alaykum, kecha buyurtma qilgan sementimiz hali kelmadi", "Usta kutib o'tiribdi, qachon olib kelasizlar?"],
     ["Uzr, mashina yo'lda buzilgan, 2 soat ichida yetkazamiz"]),
    ("shikoyat", "Mahsulot Sifati va Nuqsonlar", "negative", 2,
     ["Gipsokartonning 3 tasi singan holda keldi", "Rasmini yubordim, almashtirib berasizlarmi?"],
     ["Ko'rdik, ertaga almashtirib beramiz"]),
    ("shikoyat", "Komplektatsiya va Hajm", "negative", 2,
     ["50 qop buyurtma qilgandim, 46 ta keldi"], []),
    ("shikoyat", "Narx va To'lovlar", "negative", 1,
     ["Hisob-fakturada narx boshqacha yozilgan, kelishgan narxdan qimmat"],
     ["Tekshirib ko'raman, xato bo'lsa to'g'rilaymiz"]),
    ("maqtov", "Ijobiy Fikrlar", "positive", 0,
     ["Yetkazib berish juda tez bo'ldi, rahmat!", "Haydovchingiz ham juda xushmuomala ekan"],
     ["Rahmat, yana kutib qolamiz!"]),
    ("maqtov", "Xodimlar va Xizmat Ko'rsatish", "positive", 0,
     ["Bekzodga rahmat, to'g'ri material tanlashda yordam berdi"], ["Xursandmiz, doim xizmatingizdamiz"]),
    ("savol", "Narx va To'lovlar", "neutral", 0,
     ["Gipsokarton 12.5 mm narxi qancha?", "Ulgurji olsak chegirma bormi?"],
     ["Donasi 58 000 so'm, 100 tadan ko'piga 5% chegirma"]),
    ("savol", "Dastavka va Logistika", "neutral", 0,
     ["Sergeliga yetkazib berish necha pul?"], ["Shahar ichida 150 000 so'm, 2 tonnadan ko'p bo'lsa bepul"]),
    ("savol", "Qaytarish va Kafolat", "neutral", 1,
     ["Ishlatilmagan plitkani qaytarsa bo'ladimi?"], []),
    ("buyurtma", "Komplektatsiya va Hajm", "neutral", 0,
     ["40 qop sement M400 va 2 tonna qum kerak", "Ertaga ertalab yetkazasizlarmi?"],
     ["Bo'ladi, ertaga 9:00 da yuboramiz. Manzilni yozing", "Qabul qildik, buyurtma raqami #1043"]),
    ("buyurtma", "Dastavka va Logistika", "neutral", 0,
     ["Armatura 12 lik 1 tonna, shu hafta olib kelib berasizlarmi?"], ["Payshanba kuni yetkazamiz"]),
    ("boshqa", "Boshqa", "neutral", 0,
     ["Ish vaqtingiz shanba kuni ham bormi?"], ["Shanba 9:00 dan 15:00 gacha"]),
]
UNRELATED = [["Kecha futbolni kim ko'rdi?", "Paxtakor zo'r o'ynadi"], ["Tug'ilgan kuning muborak bo'lsin!", "Rahmat do'stim"]]


def utc(day: date, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=TZ).astimezone(timezone.utc)


def lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, datetime):
        return "'" + v.isoformat() + "'"
    if isinstance(v, date):
        return "'" + v.isoformat() + "'"
    if isinstance(v, (list, dict)):
        return "'" + json.dumps(v, ensure_ascii=False).replace("'", "''") + "'::jsonb"
    return "'" + str(v).replace("'", "''") + "'"


def arr(items: list[str]) -> str:
    return "ARRAY[" + ", ".join(lit(x) for x in items) + "]::text[]"


def build_sql(anchor: date) -> str:
    rnd = random.Random(20261026)
    days = [anchor - timedelta(days=1), anchor]
    out: list[str] = []
    add = out.append

    for chat_id, username, title in GROUPS:
        add(f"INSERT INTO core.sources (kind, tg_chat_id, tg_username, title, reader) VALUES "
            f"('supergroup', {chat_id}, {lit(username)}, {lit(title)}, 'fixture');")
    for uid, name, mgr in STAFF:
        add(f"INSERT INTO core.people (tg_user_id, display_name, role, is_manager) VALUES ({uid}, {lit(name)}, 'employee', {lit(mgr)});")
    for uid, name in CLIENTS:
        add(f"INSERT INTO core.people (tg_user_id, display_name, role) VALUES ({uid}, {lit(name)}, 'client');")
    for gi, (chat_id, _, _) in enumerate(GROUPS):
        src = f"(SELECT id FROM core.sources WHERE tg_chat_id = {chat_id})"
        add(f"INSERT INTO core.source_members (source_id, person_id, member_role, origin) VALUES "
            f"({src}, (SELECT id FROM core.people WHERE tg_user_id = {MANAGER_OF[gi]}), 'manager', 'manual'), "
            f"({src}, (SELECT id FROM core.people WHERE tg_user_id = 900003), 'admin', 'telegram');")

    msg_id = {gi: 100 for gi in range(len(GROUPS))}
    review_i = 0
    episodes = []  # build the conversation plan first
    for di, day in enumerate(days):
        for gi in range(len(GROUPS)):
            picks = rnd.sample(range(len(SCENARIOS)), 6)
            for slot, si in enumerate(picks):
                episodes.append(("rel", di, gi, si, 9 + slot + rnd.randint(0, 1), rnd.randint(0, 50)))
        episodes.append(("unrel", di, di % 3, di, 13, 5))
    episodes.append(("unsure", 1, 2, None, 16, 20))
    episodes.append(("pending", 1, 0, None, 17, 40))

    open_issues: dict[int, list[dict]] = {gi: [] for gi in range(len(GROUPS))}
    for ei, (kind, di, gi, si, hh, mm) in enumerate(episodes):
        day = days[di]
        chat_id, username, _ = GROUPS[gi]
        src = f"(SELECT id FROM core.sources WHERE tg_chat_id = {chat_id})"
        client = CLIENTS[(ei * 3 + gi) % len(CLIENTS)]
        staff = STAFF[(ei + gi) % len(STAFF)]
        t = utc(day, hh, mm)
        lines: list[tuple[int, str, datetime]] = []
        if kind == "rel":
            typ, topic, sentiment, severity, cl, st = SCENARIOS[si]
            for i, text in enumerate(cl):
                lines.append((client[0], text, t + timedelta(seconds=40 * i)))
            # reply delay: fast, late or none (scenario 2 and 8 have no staff lines)
            delay = [90, 150, 240, 720, 1500][(ei + di) % 5] if st else None
            for i, text in enumerate(st):
                lines.append((staff[0], text, t + timedelta(seconds=delay + 60 * i)))
        elif kind == "unrel":
            for i, text in enumerate(UNRELATED[si % 2]):
                lines.append((CLIENTS[(i + ei) % len(CLIENTS)][0], text, t + timedelta(seconds=50 * i)))
        elif kind == "unsure":
            lines = [(client[0], "Bugun do'kon ochiqmi?", t), (CLIENTS[0][0], "Bilmadim, men ham kelmoqchi edim", t + timedelta(minutes=2))]
        else:  # pending: a fresh request without an answer yet
            lines = [(client[0], "Kafel yelimi bormi? 20 qop kerak", t)]

        first_id = msg_id[gi] + 1
        for uid, text, at in lines:
            msg_id[gi] += 1
            name = next((n for u, n, *_ in STAFF + CLIENTS if u == uid), None)
            add(f"INSERT INTO core.messages (source_id, tg_message_id, sent_at, sender_tg_id, sender_name, text) VALUES "
                f"({src}, {msg_id[gi]}, {lit(at)}, {uid}, {lit(name)}, {lit(text)});")
        last_id = msg_id[gi]
        relevance = {"rel": "yes", "unrel": "no", "unsure": "unsure", "pending": None}[kind]
        add(f"INSERT INTO core.episodes (source_id, started_at, ended_at, first_tg_message_id, last_tg_message_id, message_count, "
            f"participant_tg_ids, relevance, relevance_confidence, segmenter_version) VALUES ({src}, {lit(lines[0][2])}, "
            f"{lit(lines[-1][2])}, {first_id}, {last_id}, {len(lines)}, ARRAY[{', '.join(str(u) for u in dict.fromkeys(x[0] for x in lines))}]::bigint[], "
            f"{lit(relevance)}, {0.92 if relevance == 'yes' else 0.55}, 'fixture-v1');")
        ep = f"(SELECT id FROM core.episodes WHERE source_id = {src} AND first_tg_message_id = {first_id} AND segmenter_version = 'fixture-v1')"
        add(f"INSERT INTO core.episode_messages (episode_id, source_id, tg_message_id, sent_at) "
            f"SELECT {ep}, source_id, tg_message_id, sent_at FROM core.messages WHERE source_id = {src} "
            f"AND tg_message_id BETWEEN {first_id} AND {last_id};")

        if kind == "rel":
            typ, topic, sentiment, severity, cl, st = SCENARIOS[si]
            needs_reply = typ in ("shikoyat", "savol", "buyurtma") and not st
            confidence = 0.55 if (ei % 7 == 3) else round(0.78 + (ei % 5) * 0.04, 2)
            evidence = [{"tg_message_id": first_id, "quote": cl[0][:60]}]
            summary = f"{typ.capitalize()}: {cl[0][:70]}"
            add(f"INSERT INTO core.analyses (episode_id, categories, primary_category, sentiment, severity, summary, evidence, "
                f"entities, needs_reply, confidence, model_id, prompt_version) VALUES ({ep}, {arr([typ])}, {lit(typ)}, "
                f"{lit(sentiment)}, {severity}, {lit(summary)}, {lit(evidence)}, {lit({'topic': topic, 'products': [], 'orders': []})}, "
                f"{lit(needs_reply)}, {confidence}, 'fixture', 'fixture-v1');")
            if confidence < 0.6:
                review_i += 1
                reviewed = review_i == 1
                add(f"INSERT INTO core.review_queue (item_type, item_id, reason, confidence, details, human_decision, reviewed_by, reviewed_at) "
                    f"SELECT 'analysis', a.id, 'low_confidence', {confidence}, {lit({'note': 'sinov'})}, "
                    f"{lit('tasdiqlandi' if reviewed else None)}, {lit('demo_admin' if reviewed else None)}, "
                    f"{lit(utc(anchor, 10, 0) if reviewed else None)} FROM core.analyses a WHERE a.episode_id = {ep};")
            if needs_reply:
                open_issues[gi].append({"tg_message_id": first_id, "issue": cl[0][:60], "since": lines[0][2].isoformat()})
            if typ in ("shikoyat", "savol", "buyurtma"):
                req_at = lines[0][2]
                answers = [x for x in lines if x[0] == staff[0]]
                deadline = req_at + timedelta(seconds=SLA_S)
                if answers:
                    ans_at = answers[0][2]
                    ans_id = first_id + len(cl)
                    if ans_at <= deadline:
                        cols = f"'answered', {ans_id}, {lit(ans_at)}, {staff[0]}, NULL"
                    else:
                        cols = f"'missed', {ans_id}, NULL, {staff[0]}, {lit(ans_at)}"
                    add(f"INSERT INTO core.response_pairs (source_id, request_tg_message_id, requested_at, requester_tg_id, episode_id, "
                        f"deadline_at, status, answer_tg_message_id, answered_at, responder_tg_id, late_answer_at, responder_person_id, "
                        f"responder_role, rule_version) SELECT {src}, {first_id}, {lit(req_at)}, {client[0]}, {ep}, {lit(deadline)}, {cols}, "
                        f"(SELECT id FROM core.people WHERE tg_user_id = {staff[0]}), 'employee', 'fixture-v1';")
                else:
                    add(f"INSERT INTO core.response_pairs (source_id, request_tg_message_id, requested_at, requester_tg_id, episode_id, "
                        f"deadline_at, status, rule_version) VALUES ({src}, {first_id}, {lit(req_at)}, {client[0]}, {ep}, {lit(deadline)}, "
                        f"'missed', 'fixture-v1');")
        elif kind == "pending":
            add(f"INSERT INTO core.response_pairs (source_id, request_tg_message_id, requested_at, requester_tg_id, episode_id, "
                f"deadline_at, status, rule_version) VALUES ({src}, {first_id}, {lit(lines[0][2])}, {client[0]}, {ep}, "
                f"{lit(lines[0][2] + timedelta(seconds=SLA_S))}, 'pending', 'fixture-v1');")
            open_issues[gi].append({"tg_message_id": first_id, "issue": "Kafel yelimi so'radi, javob yo'q", "since": lines[0][2].isoformat()})

    # two voice messages with transcripts (W3 [CONTRACT] 7)
    for n, (gi, hh, text) in enumerate([(0, 11, "Sement M500 bormi, narxini aytib yuboring"), (1, 15, "Mashina darvoza oldida turibdi")]):
        chat_id = GROUPS[gi][0]
        src = f"(SELECT id FROM core.sources WHERE tg_chat_id = {chat_id})"
        msg_id[gi] += 1
        at = utc(days[1], hh, 30)
        add(f"INSERT INTO core.messages (source_id, tg_message_id, sent_at, sender_tg_id, sender_name, kind) VALUES "
            f"({src}, {msg_id[gi]}, {lit(at)}, {CLIENTS[n][0]}, {lit(CLIENTS[n][1])}, 'voice');")
        add(f"INSERT INTO core.media (source_id, tg_message_id, message_sent_at, kind, status, path, bytes, mime, duration_s) VALUES "
            f"({src}, {msg_id[gi]}, {lit(at)}, 'voice', 'downloaded', 'TEST/fixture/voice_{n + 1}.ogg', 24000, 'audio/ogg', 7.5);")
        add(f"INSERT INTO core.transcripts (media_id, provider, model, message_sent_at, source_id, tg_message_id, language, text, confidence) "
            f"SELECT m.id, 'fixture', 'none', {lit(at)}, m.source_id, m.tg_message_id, 'uz', {lit(text)}, 0.9 FROM core.media m "
            f"WHERE m.source_id = {src} AND m.tg_message_id = {msg_id[gi]};")

    for gi, (chat_id, _, title) in enumerate(GROUPS):
        add(f"INSERT INTO core.chat_context (source_id, summary, open_issues, window_start, window_end, model_id, prompt_version) VALUES "
            f"((SELECT id FROM core.sources WHERE tg_chat_id = {chat_id}), {lit('Sinov: ' + title + ' guruhida oxirgi 24 soat')}, "
            f"{lit(open_issues[gi])}, {lit(utc(days[0], 18, 0))}, {lit(utc(days[1], 18, 0))}, 'fixture', 'fixture-v1');")

    for day in days:  # daily aggregates from the analyses above
        add(f"""INSERT INTO core.daily_stats (local_day, source_id, category, episode_count, negative_count, needs_reply_count)
SELECT e.local_day, e.source_id, c, count(*), count(*) FILTER (WHERE a.sentiment = 'negative'), count(*) FILTER (WHERE a.needs_reply)
FROM core.analyses a JOIN core.episodes e ON e.id = a.episode_id CROSS JOIN LATERAL unnest(a.categories) c
WHERE a.is_current AND e.relevance = 'yes' AND e.local_day = {lit(day)} GROUP BY 1, 2, 3;""")
        add(f"INSERT INTO core.daily_summaries (local_day, scope, scope_key, summary, model_id, prompt_version) VALUES "
            f"({lit(day)}, 'all', '', {lit(f'Sinov xulosasi {day}: yetkazib berish kechikishi va narx savollari ko`p.')}, 'fixture', 'fixture-v1'), "
            f"({lit(day)}, 'category', 'shikoyat', 'Sinov: shikoyatlar asosan dastavka va sifat bo''yicha.', 'fixture', 'fixture-v1');")

    add(f"INSERT INTO core.report_deliveries (report_type, local_day, target_chat, status, attempts, file_path, sent_at) VALUES "
        f"('tg_daily_excel', {lit(days[0])}, 'TEST report group', 'sent', 1, 'TEST/hisobot_{days[0]}.xlsx', {lit(utc(anchor, 0, 10))});")
    for typ, status, gpu, n in [("transcribe", "done", "none", 2), ("analyze_episode", "done", "llm", 24), ("analyze_episode", "queued", "llm", 2),
                                ("analyze_image", "dead", "llm", 1), ("camera_day_batch", "done", "vision", 1)]:
        for k in range(n):
            add(f"INSERT INTO core.jobs (type, payload, status, gpu_class, attempts, run_after, idempotency_key, last_error) VALUES "
                f"({lit(typ)}, {lit({'fixture': True, 'n': k})}, {lit(status)}, {lit(gpu)}, {5 if status == 'dead' else 1}, "
                f"{lit(utc(anchor, 9, k))}, {lit(f'fixture:{typ}:{status}:{k}')}, {lit('sinov xatosi' if status == 'dead' else None)});")
    for comp, status in [("w1_ingest", "ok"), ("w2_analyst", "ok"), ("w4_recorder", "degraded")]:
        add(f"INSERT INTO core.heartbeats (component, last_beat_at, started_at, status, version, details) VALUES "
            f"({lit(comp)}, {lit(utc(anchor, 18, 0))}, {lit(utc(anchor, 7, 0))}, {lit(status)}, 'fixture', {lit({'fixture': True})});")

    # camera: 4 anonymous staff, 2 days, recording 07:00-22:00 in 10-minute segments (one gap per day)
    for p in range(1, 5):
        add(f"INSERT INTO core.anon_persons (label, scope, staff_no, first_seen_at, last_seen_at, embedding_model) VALUES "
            f"('Hodim {p}', 'permanent', {p}, {lit(utc(days[0], 8, 50))}, {lit(utc(days[1], 18, 40))}, 'fixture');")
    for day in days:
        for k in range(90):
            st = utc(day, 7, 0) + timedelta(minutes=10 * k)
            if k == 40:
                continue  # gap 13:40-13:50
            add(f"INSERT INTO core.video_segments (camera_id, local_day, file, started_at, ended_at, duration_s, bytes, session, seq, "
                f"break_before, gap_before_s, end_source, closed_reason, codec, width, height, measured_fps, manifest_v) VALUES "
                f"('camera1', {lit(day)}, '{st:%Y%m%dT%H%M%S}Z.mkv', {lit(st)}, {lit(st + timedelta(minutes=10))}, 600, 2600000, 'fixture', {k}, "
                f"{lit(k == 41)}, {600 if k == 41 else 0}, 'list', 'segment', 'hevc', 640, 360, 20, 1);")
        add(f"INSERT INTO core.video_gaps (camera_id, local_day, from_at, to_at, duration_s, reason) VALUES "
            f"('camera1', {lit(day)}, {lit(utc(day, 13, 40))}, {lit(utc(day, 13, 50))}, 600, 'ffmpeg_exit');")

        for p in range(1, 5):
            arrive = utc(day, 8, 45) + timedelta(minutes=rnd.randint(0, 35))
            leave = utc(day, 18, 0) + timedelta(minutes=rnd.randint(0, 45))
            breaks = sorted(rnd.sample(range(10, 17), rnd.randint(2, 3)))
            moments = [("in", arrive)]
            for b in breaks:
                out_at = utc(day, b, rnd.randint(0, 40))
                moments += [("out", out_at), ("in", out_at + timedelta(minutes=rnd.randint(5, 25)))]
            missing_exit = (p == 3 and day == days[1])
            if not missing_exit:
                moments.append(("out", leave))
            person = f"(SELECT id FROM core.anon_persons WHERE label = 'Hodim {p}')"
            for j, (direction, at) in enumerate(moments):
                inferred = (p == 2 and j == 2)
                method = "recovered" if inferred else ("zone_appear" if direction == "in" else "zone_disappear")
                add(f"INSERT INTO core.camera_events (camera_id, at, direction, method, track_id, anon_person_id, confidence, inferred, "
                    f"analysis_run) VALUES ('camera1', {lit(at)}, '{direction}', '{method}', 't{p}-{j}', {person}, "
                    f"{0.6 if inferred else 0.93}, {lit(inferred)}, 'fixture-v1');")
                frame = "\\".join(["E:", "data", "camera_events", f"{day:%Y}", f"{day:%m}", f"{day:%d}", "camera1",
                                   f"fixture_h{p}_{j}_{direction}.jpg"])  # D-29 layout, file does not exist
                add(f"INSERT INTO core.camera_event_frames (event_id, kind, frame_at, file_path, width, height) "
                    f"SELECT id, '{direction}', at, {lit(frame)}, 640, 360 FROM core.camera_events "
                    f"WHERE analysis_run = 'fixture-v1' AND track_id = 't{p}-{j}' AND at = {lit(at)};")
            room = 0.0
            sessions = 0
            for j in range(0, len(moments), 2):
                start = moments[j][1]
                end = moments[j + 1][1] if j + 1 < len(moments) else None
                ev = lambda k: (f"(SELECT id FROM core.camera_events WHERE analysis_run = 'fixture-v1' AND track_id = 't{p}-{k}' "
                                f"AND at = {lit(moments[k][1])})")
                add(f"INSERT INTO core.presence_sessions (camera_id, anon_person_id, local_day, started_at, ended_at, enter_event_id, "
                    f"exit_event_id, inferred, confidence, analysis_run) VALUES ('camera1', {person}, {lit(day)}, {lit(start)}, {lit(end)}, "
                    f"{ev(j)}, {ev(j + 1) if end else 'NULL'}, {lit(p == 2 and j + 1 == 2)}, 0.9, 'fixture-v1');")
                sessions += 1
                if end:
                    room += (end - start).total_seconds() / 60
            last_out = None if missing_exit else leave
            office = round((last_out - arrive).total_seconds() / 60, 1) if last_out else None
            add(f"INSERT INTO core.daily_presence (local_day, anon_person_id, camera_id, first_in_at, last_out_at, office_minutes, "
                f"room_minutes, away_minutes, session_count, uncertain, uncertainty_note, analysis_run) VALUES ({lit(day)}, {person}, 'camera1', "
                f"{lit(arrive)}, {lit(last_out)}, {office if office is not None else 'NULL'}, {round(room, 1)}, "
                f"{round(office - room, 1) if office is not None else 'NULL'}, {sessions}, {lit(missing_exit)}, "
                f"{lit('oxirgi chiqish ko`rilmadi' if missing_exit else None)}, 'fixture-v1');")
    return "\n".join(out) + "\n"
