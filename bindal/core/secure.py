"""The runs' encrypted output (scripts/github/ci_run.sh: openssl enc with ARTIFACT_KEY), read back."""
from __future__ import annotations

from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def decrypt(blob: bytes, password: str) -> bytes:
    """openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt (SHA-256) in reverse."""
    if blob[:8] != b"Salted__":
        raise ValueError("not an openssl-encrypted file")
    salt, body = blob[8:16], blob[16:]
    key_iv = PBKDF2HMAC(hashes.SHA256(), 48, salt, 200000).derive(password.encode())
    dec = Cipher(algorithms.AES(key_iv[:32]), modes.CBC(key_iv[32:])).decryptor()
    padded = dec.update(body) + dec.finalize()
    unpad = padding.PKCS7(128).unpadder()
    return unpad.update(padded) + unpad.finalize()
