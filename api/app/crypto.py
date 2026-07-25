"""Symmetric encryption for farmer PII at rest (Fernet = AES-128-CBC + HMAC).

Names, phones and national IDs are stored only as ciphertext in the database;
they are decrypted in memory for the narrow, role-scoped views that are allowed
to see them. The key comes from AEZ_PII_KEY. In production that MUST be a real,
secret, backed-up value — lose it and the PII is unrecoverable; leak it and the
at-rest encryption is worthless. For local dev we derive a deterministic key
from a default secret so the app runs out of the box (NOT secret — set the env
var for anything real).
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

_fernet: Fernet | None = None


def _key() -> bytes:
    raw = os.environ.get("AEZ_PII_KEY")
    if raw:
        # Accept either a proper Fernet key or any passphrase (we hash it to 32B).
        try:
            Fernet(raw.encode())
            return raw.encode()
        except Exception:
            return base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    # Dev fallback — deterministic, explicitly not secret.
    return base64.urlsafe_b64encode(hashlib.sha256(b"aez-dev-pii-key").digest())


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_key())
    return _fernet


def encrypt(plaintext: str | None) -> str | None:
    if plaintext is None or plaintext == "":
        return None
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _cipher().decrypt(token.encode()).decode()
    except InvalidToken:
        return None
