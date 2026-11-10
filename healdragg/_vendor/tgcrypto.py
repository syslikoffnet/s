"""Мини-замена tgcrypto: только IGE-256 на чистом pyaes (без нативных колёс)."""
from __future__ import annotations

import pyaes


def _aes_ecb_encrypt(key: bytes, block: bytes) -> bytes:
    return pyaes.AES(key).encrypt(block)


def _aes_ecb_decrypt(key: bytes, block: bytes) -> bytes:
    return pyaes.AES(key).decrypt(block)


def ige256_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    cipher = pyaes.AES(key)
    prev_c = bytes(iv[16:32])
    prev_p = bytes(iv[0:16])
    out = bytearray()
    for offset in range(0, len(data), 16):
        block = bytes(data[offset : offset + 16])
        xored = bytes(a ^ b for a, b in zip(block, prev_c, strict=False))
        encrypted = cipher.encrypt(xored)
        chunk = bytes(a ^ b for a, b in zip(encrypted, prev_p, strict=False))
        out.extend(chunk)
        prev_p, prev_c = block, chunk
    return bytes(out)


def ige256_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    cipher = pyaes.AES(key)
    prev_c = bytes(iv[16:32])
    prev_p = bytes(iv[0:16])
    out = bytearray()
    for offset in range(0, len(data), 16):
        block = bytes(data[offset : offset + 16])
        xored = bytes(a ^ b for a, b in zip(block, prev_p, strict=False))
        decrypted = cipher.decrypt(xored)
        chunk = bytes(a ^ b for a, b in zip(decrypted, prev_c, strict=False))
        out.extend(chunk)
        prev_c, prev_p = block, chunk
    return bytes(out)
