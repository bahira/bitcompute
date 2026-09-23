"""Small authenticated wire format for trusted Bitcompute swarms.

Ed25519 signatures bind manifests/results to pinned node keys. AES-256-GCM
provides payload confidentiality when all job participants share an out-of-band
key. This module is not a substitute for key distribution or OS isolation.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

WIRE_VERSION = 1
NONCE_BYTES = 12
KEY_BYTES = 32
MAX_ENVELOPE_BYTES = 128 * 1024 * 1024


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _b64e(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64d(value: Any, field: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"invalid security envelope {field}")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError(f"invalid security envelope {field}") from exc


def _atomic_write(path: str | Path, data: bytes, mode: int) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        if os.name != "nt":
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def public_key_bytes(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def key_fingerprint(public_key: Ed25519PublicKey | bytes) -> str:
    raw = public_key if isinstance(public_key, bytes) else public_key_bytes(public_key)
    if len(raw) != 32:
        raise ValueError("Ed25519 public keys must be 32 bytes")
    return hashlib.sha256(raw).hexdigest()


def generate_identity(private_path: str | Path, *, overwrite: bool = False) -> tuple[Path, Path, str]:
    """Create a PEM private key and adjacent public key; never overwrite by default."""
    private_file = Path(private_path).expanduser()
    public_file = Path(f"{private_file}.pub")
    if not overwrite and (private_file.exists() or public_file.exists()):
        raise FileExistsError(f"identity key already exists: {private_file}")
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    _atomic_write(private_file, private_pem, 0o600)
    _atomic_write(public_file, public_pem, 0o644)
    return private_file, public_file, key_fingerprint(public_key)


def generate_encryption_key(path: str | Path, *, overwrite: bool = False) -> Path:
    target = Path(path).expanduser()
    if not overwrite and target.exists():
        raise FileExistsError(f"encryption key already exists: {target}")
    _atomic_write(target, os.urandom(KEY_BYTES), 0o600)
    return target


def load_private_key(path: str | Path) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(Path(path).expanduser().read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"cannot load Ed25519 private key from {path}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(f"not an Ed25519 private key: {path}")
    return key


def load_public_key(path: str | Path) -> Ed25519PublicKey:
    try:
        raw = Path(path).expanduser().read_bytes()
        try:
            key = serialization.load_pem_public_key(raw)
        except ValueError:
            key = Ed25519PublicKey.from_public_bytes(raw)
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"cannot load Ed25519 public key from {path}") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError(f"not an Ed25519 public key: {path}")
    return key


def load_encryption_key(path: str | Path) -> bytes:
    try:
        raw = Path(path).expanduser().read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read encryption key from {path}") from exc
    if len(raw) != KEY_BYTES:
        try:
            decoded = bytes.fromhex(raw.decode("ascii").strip())
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("encryption key must be 32 raw bytes or 64 hexadecimal characters") from exc
        raw = decoded
    if len(raw) != KEY_BYTES:
        raise ValueError("encryption key must be 32 bytes")
    return raw


def sign_envelope(
    payload: bytes,
    identity: Ed25519PrivateKey,
    *,
    purpose: str,
    encryption_key: bytes | None = None,
) -> bytes:
    """Sign and optionally encrypt bytes into a canonical JSON envelope."""
    if not isinstance(payload, bytes):
        raise TypeError("security envelope payload must be bytes")
    if not isinstance(purpose, str) or not purpose or len(purpose) > 128:
        raise ValueError("security envelope purpose is invalid")
    signer = identity.public_key()
    signer_bytes = public_key_bytes(signer)
    fingerprint = key_fingerprint(signer_bytes)
    encrypted = encryption_key is not None
    if encrypted:
        if len(encryption_key) != KEY_BYTES:
            raise ValueError("AES-256-GCM key must be exactly 32 bytes")
        nonce = os.urandom(NONCE_BYTES)
        aad = f"bitcompute:{WIRE_VERSION}:{purpose}:{fingerprint}".encode()
        content = nonce + AESGCM(encryption_key).encrypt(nonce, payload, aad)
    else:
        content = payload
    unsigned: dict[str, Any] = {
        "version": WIRE_VERSION,
        "purpose": purpose,
        "signer": fingerprint,
        "public_key": _b64e(signer_bytes),
        "encrypted": encrypted,
        "payload": _b64e(content),
    }
    signature = identity.sign(_canonical_json(unsigned))
    return _canonical_json({**unsigned, "signature": _b64e(signature)})


def verify_envelope(
    raw: bytes,
    *,
    purpose: str,
    trusted_public_key: Ed25519PublicKey | None = None,
    encryption_key: bytes | None = None,
    require_encryption: bool = True,
) -> tuple[bytes, Ed25519PublicKey, str]:
    """Verify signer/purpose and decrypt the signed payload, if encrypted."""
    if not isinstance(raw, bytes) or len(raw) > MAX_ENVELOPE_BYTES:
        raise ValueError("security envelope exceeds size limit")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid security envelope JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("security envelope root must be an object")
    required = {"version", "purpose", "signer", "public_key", "encrypted", "payload", "signature"}
    if set(value) != required:
        raise ValueError("security envelope fields are invalid")
    if value["version"] != WIRE_VERSION or value["purpose"] != purpose:
        raise ValueError("security envelope version or purpose mismatch")
    if not isinstance(value["encrypted"], bool):
        raise ValueError("security envelope encryption flag is invalid")

    public_bytes = _b64d(value["public_key"], "public_key")
    if len(public_bytes) != 32:
        raise ValueError("invalid Ed25519 public key length")
    public_key = Ed25519PublicKey.from_public_bytes(public_bytes)
    fingerprint = key_fingerprint(public_bytes)
    if value["signer"] != fingerprint:
        raise ValueError("security envelope signer fingerprint mismatch")
    if trusted_public_key is not None and public_key_bytes(trusted_public_key) != public_bytes:
        raise ValueError("untrusted signer key")

    signature = _b64d(value["signature"], "signature")
    unsigned = {key: value[key] for key in required - {"signature"}}
    try:
        public_key.verify(signature, _canonical_json(unsigned))
    except InvalidSignature as exc:
        raise ValueError("security envelope signature is invalid") from exc

    encrypted = value["encrypted"]
    if require_encryption and not encrypted:
        raise ValueError("unencrypted security envelope rejected")
    content = _b64d(value["payload"], "payload")
    if encrypted:
        if encryption_key is None or len(encryption_key) != KEY_BYTES:
            raise ValueError("a 32-byte encryption key is required")
        if len(content) < NONCE_BYTES + 16:
            raise ValueError("encrypted security envelope is truncated")
        nonce, ciphertext = content[:NONCE_BYTES], content[NONCE_BYTES:]
        aad = f"bitcompute:{WIRE_VERSION}:{purpose}:{fingerprint}".encode()
        try:
            content = AESGCM(encryption_key).decrypt(nonce, ciphertext, aad)
        except InvalidTag as exc:
            raise ValueError("security envelope authentication failed") from exc
    elif encryption_key is not None:
        raise ValueError("plaintext security envelope rejected when encryption is configured")
    return content, public_key, fingerprint
