"""Verify that a release manifest was signed by whoever holds this project's key.

What an updater has to know is that the bytes it is about to run came from the
same place the last ones did. A signature over the release manifest answers
exactly that, with a key the Owner generates and keeps, and Windows verifies it
through CNG -- no new dependency and no hand-written crypto.

It is the update channel's release-identity proof. It says nothing to
SmartScreen and nothing to a user installing for the first time, who may still
see an unknown publisher. That first-run Windows behavior is deliberately
outside this updater's trust decision.

Key format is what .NET's ECDsa hands back for nistP256 -- Q.X and Q.Y, 32
bytes each, hex, concatenated -- and the signature is the raw r||s pair that
`SignData(..., SHA256)` produces. The private half never enters this repository.
"""

from __future__ import annotations

import ctypes
import hashlib
import struct

# BCRYPT_ECDSA_PUBLIC_P256_MAGIC, little-endian "ECS1".
_P256_PUBLIC_MAGIC = 0x31534345
_COORDINATE_BYTES = 32
_SIGNATURE_BYTES = 64
_STATUS_SUCCESS = 0


def _key_blob(public_key_hex: str) -> bytes:
    raw = bytes.fromhex(public_key_hex.strip())
    if len(raw) != _COORDINATE_BYTES * 2:
        raise ValueError("release public key must be P-256 X||Y, 64 bytes")
    return struct.pack("<II", _P256_PUBLIC_MAGIC, _COORDINATE_BYTES) + raw


def verify_release_signature(payload: bytes, signature: bytes, public_key_hex: str) -> bool:
    """Whether `signature` is this key's ECDSA P-256 signature over `payload`.

    Fails closed: an unusable key, a wrong-length signature, a platform without
    CNG, or any error from it, is not a signature.
    """
    if not public_key_hex or len(signature) != _SIGNATURE_BYTES:
        return False
    try:
        blob = _key_blob(public_key_hex)
    except ValueError:
        return False
    try:
        bcrypt = ctypes.WinDLL("bcrypt.dll")
    except (OSError, AttributeError):
        return False

    algorithm = ctypes.c_void_p()
    status = bcrypt.BCryptOpenAlgorithmProvider(
        ctypes.byref(algorithm), ctypes.c_wchar_p("ECDSA_P256"), None, 0
    )
    if status != _STATUS_SUCCESS:
        return False
    key = ctypes.c_void_p()
    try:
        status = bcrypt.BCryptImportKeyPair(
            algorithm, None, ctypes.c_wchar_p("ECCPUBLICBLOB"),
            ctypes.byref(key), blob, len(blob), 0,
        )
        if status != _STATUS_SUCCESS:
            return False
        digest = hashlib.sha256(payload).digest()
        try:
            status = bcrypt.BCryptVerifySignature(
                key, None, digest, len(digest), signature, len(signature), 0
            )
        finally:
            bcrypt.BCryptDestroyKey(key)
        return status == _STATUS_SUCCESS
    finally:
        bcrypt.BCryptCloseAlgorithmProvider(algorithm, 0)
