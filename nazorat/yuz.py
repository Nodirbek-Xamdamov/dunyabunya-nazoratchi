"""Yuz bo'yicha tanish (lokal, CPU): YuNet yuzni topadi, ArcFace (buffalo_l w600k_r50) vektor chiqaradi.

Yuz faqat odam qutisining bosh qismidan qidiriladi (to'liq o'lchamli kadrdan), 5 nuqta bo'yicha
112x112 ga tekislanadi. Natija — 512 o'lchamli, uzunligi 1 vektor; bir odam ~0.4 dan yuqori.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# ArcFace tekislash shabloni (insightface)
_REF = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                 [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)


@dataclass
class YuzNatija:
    vec: np.ndarray
    sifat: float          # 0..1: aniqlik ishonchi x o'lcham
    olcham: float         # yuz kengligi, to'liq kadr pikselida
    rasm: np.ndarray      # bosh qismi (panel uchun)


class Yuz:
    def __init__(self, det_path: str, rec_path: str, min_px: float = 26.0):
        self.det = cv2.FaceDetectorYN.create(det_path, "", (320, 320), 0.75, 0.3, 20)
        self.rec = cv2.dnn.readNetFromONNX(rec_path)
        self.min_px = min_px

    def top(self, full: np.ndarray, box) -> YuzNatija | None:
        """box: odam qutisi to'liq kadr koordinatalarida."""
        H, W = full.shape[:2]
        x1, y1, x2, y2 = box
        w, h = x2 - x1, y2 - y1
        hx1, hx2 = int(max(0, x1 - 0.15 * w)), int(min(W, x2 + 0.15 * w))
        hy1, hy2 = int(max(0, y1 - 0.1 * h)), int(min(H, y1 + 0.55 * h))
        crop = full[hy1:hy2, hx1:hx2]
        if crop.size == 0 or crop.shape[0] < 20 or crop.shape[1] < 20:
            return None
        s = min(3.0, max(1.0, 240.0 / crop.shape[1]))
        cs = cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC) if s > 1 else crop
        self.det.setInputSize((cs.shape[1], cs.shape[0]))
        _, faces = self.det.detect(cs)
        if faces is None or len(faces) == 0:
            return None
        f = max(faces, key=lambda r: r[14] * r[2])
        fw = f[2] / s
        if fw < self.min_px:
            return None
        lm = f[4:14].reshape(5, 2).astype(np.float32)
        # yonlama yuz: burun ko'zlar orasida bo'lmasa — tashlaymiz
        ex1, ex2 = sorted([lm[0, 0], lm[1, 0]])
        if not (ex1 - 0.1 * (ex2 - ex1) <= lm[2, 0] <= ex2 + 0.1 * (ex2 - ex1)):
            return None
        M, _ = cv2.estimateAffinePartial2D(lm, _REF, method=cv2.LMEDS)
        if M is None:
            return None
        aligned = cv2.warpAffine(cs, M, (112, 112), borderValue=0)
        blob = cv2.dnn.blobFromImage(aligned, 1 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
        self.rec.setInput(blob)
        v = self.rec.forward()[0].astype(np.float32)
        v /= np.linalg.norm(v) + 1e-9
        sifat = float(f[14]) * min(1.0, fw / 60.0)
        return YuzNatija(v, sifat, float(fw), crop.copy())
