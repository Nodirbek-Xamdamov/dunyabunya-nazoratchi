"""Menejerlarning Odoo API kalitlarini bazada ochiq matnda saqlamaslik uchun — Fernet
(simmetrik, AES asosida) shifrlash. `dbagent`dagi bir xil naqsh (`ENCRYPTION_KEY` nomi ham
o'sha yerdan)."""

from __future__ import annotations

from cryptography.fernet import Fernet

from app.config import settings

_fernet = Fernet(settings.ENCRYPTION_KEY.encode())


def encrypt(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()
