"""Encryption for sensitive stored data (MT5 passwords).

Uses Fernet (AES-128-CBC + HMAC). The key comes from settings.encryption_key.
In production this MUST be set to a real generated key. If unset, we derive a
stable dev key so the app runs locally — but that key is NOT secret, so never
rely on it for real user credentials.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet

from backend.config import settings
from backend.logging_config import get_logger

log = get_logger("security")


def _fernet() -> Fernet:
    key = settings.encryption_key
    if key:
        return Fernet(key.encode() if isinstance(key, str) else key)
    # Dev fallback: derive a deterministic (NON-SECRET) key. Warn loudly.
    log.warning(
        "ENCRYPTION_KEY not set — using an insecure derived dev key. "
        "Set ENCRYPTION_KEY before storing real credentials."
    )
    digest = hashlib.sha256(b"zanzer-insecure-dev-key").digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
