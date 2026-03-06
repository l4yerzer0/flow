from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Dict

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


META_PATH = Path("config/.credentials_meta.json")
_VERIFIER = "flow_credentials_verifier_v1"
_SENSITIVE_TOKENS = ("key", "secret", "token", "password", "passphrase", "private")
_ENC_PREFIX = "enc:v1:"
_fernet: Fernet | None = None


def has_master_password() -> bool:
    return META_PATH.exists()


def _derive_fernet(password: str, salt: bytes) -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=390000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))
    return Fernet(key)


def _write_meta(meta: dict) -> None:
    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _read_meta() -> dict:
    raw = META_PATH.read_text(encoding="utf-8-sig")
    return json.loads(raw)


def initialize_master_password(password: str) -> None:
    """Initialize encryption key from password, creating metadata on first run."""
    global _fernet

    if not password:
        raise ValueError("Master password cannot be empty.")

    if not META_PATH.exists():
        salt = os.urandom(16)
        fernet = _derive_fernet(password, salt)
        verifier = fernet.encrypt(_VERIFIER.encode("utf-8")).decode("utf-8")
        meta = {
            "version": 1,
            "kdf": "pbkdf2_sha256",
            "iterations": 390000,
            "salt_b64": base64.b64encode(salt).decode("ascii"),
            "verifier": verifier,
        }
        _write_meta(meta)
        _fernet = fernet
        return

    meta = _read_meta()
    salt = base64.b64decode(meta["salt_b64"])
    fernet = _derive_fernet(password, salt)
    try:
        plain = fernet.decrypt(meta["verifier"].encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Invalid master password.") from exc
    if plain != _VERIFIER:
        raise ValueError("Invalid master password.")
    _fernet = fernet


def _require_fernet() -> Fernet:
    if _fernet is None:
        raise RuntimeError("Master password is not initialized.")
    return _fernet


def is_sensitive_param(param_name: str) -> bool:
    lower = param_name.lower()
    return any(token in lower for token in _SENSITIVE_TOKENS)


def is_encrypted_value(value: str) -> bool:
    return isinstance(value, str) and value.startswith(_ENC_PREFIX)


def encrypt_value(value: str) -> str:
    if value == "":
        return value
    if is_encrypted_value(value):
        return value
    token = _require_fernet().encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{_ENC_PREFIX}{token}"


def decrypt_value(value: str) -> str:
    if not is_encrypted_value(value):
        return value
    token = value[len(_ENC_PREFIX) :]
    return _require_fernet().decrypt(token.encode("utf-8")).decode("utf-8")


def encrypt_params(params: Dict[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in params.items():
        value_str = str(value) if value is not None else ""
        out[key] = encrypt_value(value_str) if is_sensitive_param(key) else value_str
    return out


def decrypt_params(params: Dict[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in params.items():
        value_str = str(value) if value is not None else ""
        out[key] = decrypt_value(value_str) if is_sensitive_param(key) else value_str
    return out

