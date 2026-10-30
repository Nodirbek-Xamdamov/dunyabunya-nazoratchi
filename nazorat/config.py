"""Sozlamalar. Kamera manzili faqat .env da (CAMERA_RTSP_URL yoki CAMERA_IP/USER/PASSWORD)."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
CLIPS = DATA / "kliplar"
DB_PATH = DATA / "nazorat.db"
LOGS = DATA / "logs"
MODELS = BASE / "models"
YOLO_WEIGHTS = MODELS / "yolo26m.pt"
REID_WEIGHTS = MODELS / "osnet_x1_0_msmt17.pth"
YUZ_DET = MODELS / "yunet_2023mar.onnx"
YUZ_REC = MODELS / "arcface_w600k_r50.onnx"
RASMLAR = DATA / "rasmlar"
STOP_FILE = DATA / "STOP"


def _read_env() -> dict[str, str]:
    env = {}
    p = BASE / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def rtsp_url() -> str:
    env = {**_read_env(), **{k: v for k, v in os.environ.items() if k.startswith("CAMERA_")}}
    ip = env.get("CAMERA_IP", "")
    if ip:
        user = quote(env.get("CAMERA_USER", ""), safe="")
        pw = quote(env.get("CAMERA_PASSWORD", ""), safe="")
        port = env.get("CAMERA_PORT", "554") or "554"
        chan = env.get("CAMERA_CHANNEL", "101") or "101"
        return f"rtsp://{user}:{pw}@{ip}:{port}/Streaming/Channels/{chan}"
    url = env.get("CAMERA_RTSP_URL", "")
    if not url:
        raise SystemExit(".env da CAMERA_RTSP_URL yoki CAMERA_IP yozilmagan")
    return url


def mask(url: str) -> str:
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***:***@{rest.split('@', 1)[1]}"
    return url


# Tahlil kadri o'lchami (kamera 1920x1080 dan kichraytiriladi). Barcha koordinatalar shu o'lchamda.
FRAME_W, FRAME_H = 1280, 720
# Klip kadri o'lchami
CLIP_W, CLIP_H = 960, 540

# Xonadan chiqish joylari (ko'pburchaklar). Odam shu joyda ko'zdan yo'qolsa — "chiqdi".
ESHIK_ZONALARI: dict[str, list[tuple[int, int]]] = {
    # uzoqdagi shisha eshik va uning oldi
    "eshik": [(370, 120), (660, 120), (660, 360), (370, 360)],
    # kamera ostidagi chap pastki burchak (oynaning chap/past cheti)
    "kamera_osti": [(0, 380), (330, 380), (330, 720), (0, 720)],
}

DET_CONF = 0.30          # detektor chegarasi (tracker uchun)
MIN_BOX_H = 110          # bundan past qutilar — shisha ortidagi qo'shni xona, hisobga olinmaydi
MIN_FOOT_Y = 290         # oyog'i bundan yuqorida — shisha devor/eshikning narigi tomonida (xonada emas)
CONFIRM_S = 1.0          # yangi iz kamida shuncha ko'rinsa, hodim deb hisoblanadi
CONFIRM_MIN_CONF = 0.45  # yangi izning o'rtacha ishonchi
CONFIRM_N = 8            # shuncha kadrda ko'rinsin (22 kadr/s da ~0.4 s)
LOST_S = 2.0             # iz shuncha ko'rinmasa — yo'qoldi
MIN_ABSENCE_S = 60       # bundan qisqa chiqib-kirishlar hisobga olinmaydi (eshik oldida turish va h.k.)
LOST_TIMEOUT_S = 15 * 60  # xonada yo'qolib, shuncha ko'rinmagan hodim — chiqdi deb yoziladi

# Xonaning xodimi = stolida doimiy o'tiradigan odam. Farrosh, qorovul, boshqa xonadan kelganlar
# yuradi yoki qisqa turadi — ular xodim bo'lmaydi.
OTIRISH_R = 40           # odam qutisining markazi shu radius (piksel, kamida) ichida qolsa — joyida
OTIRISH_OYNA_S = 120     # shuncha qimirlamay tursa, "o'tiribdi" hisoblana boshlaydi
XODIM_OTIRISH_S = 30 * 60  # bir kunda shuncha o'tirgan noma'lum odam avtomatik xodim bo'ladi

# Ko'rinish o'xshashligi chegaralari (OSNet, 0..1)
SIM_DAVOM = 0.45   # xonada yo'qolgan hodim qayta topilganda
SIM_QAYTDI = 0.55  # bugun chiqib ketgan hodim qaytganda
# kiyim faqat shu kun ichida solishtiriladi (ertasi kuni kiyim boshqa bo'lishi mumkin)

# Yuz (ArcFace, 0..1) — kunlar orasida ham ishlaydi, asosiy tanish usuli
YUZ_T = 0.42        # shu va undan yuqori — o'sha odam
YUZ_FARQ = 0.07     # eng yaxshi va ikkinchi nomzod orasidagi farq kamida shuncha bo'lsin
YUZ_QOSHISH = 0.30  # odamning yuz to'plamiga yangi yuz faqat shunga o'xshasa qo'shiladi
YUZ_MAX = 200       # har odam uchun saqlanadigan eng sifatli yuzlar
YUZ_ORALIQ = 0.3    # bitta izda yuz qidirish oralig'i, s
YUZ_IZ_MAX = 25     # bitta izdan olinadigan yuzlar soni
GALLERY_MAX = 120  # har hodim uchun saqlanadigan ko'rinish vektorlari

CLIP_OLDIN = 1.0   # kirish klipi: iz paydo bo'lishidan oldin / keyin
CLIP_KEYIN = 4.0
EXIT_OLDIN = 4.0   # chiqish klipi: oxirgi ko'rinishdan oldin / keyin
EXIT_KEYIN = 1.0
BUFFER_S = 10.0
KLIP_SAQLASH_KUN = 30  # kirish/chiqish kliplari va kadrlari shuncha kundan keyin o'chiriladi
