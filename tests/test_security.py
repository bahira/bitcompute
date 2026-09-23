from __future__ import annotations

import os

import pytest

from bitcompute import security


def test_identity_keygen_load_and_permissions(tmp_path):
    private_path, public_path, fingerprint = security.generate_identity(tmp_path / "node.key")
    private_key = security.load_private_key(private_path)
    public_key = security.load_public_key(public_path)
    assert security.key_fingerprint(public_key) == fingerprint
    assert security.public_key_bytes(private_key.public_key()) == security.public_key_bytes(public_key)
    if os.name != "nt":
        assert private_path.stat().st_mode & 0o777 == 0o600
        assert public_path.stat().st_mode & 0o777 == 0o644
    with pytest.raises(FileExistsError):
        security.generate_identity(private_path)


def test_authenticated_encrypted_envelope_roundtrip(tmp_path):
    private_path, public_path, _ = security.generate_identity(tmp_path / "coordinator.key")
    private_key = security.load_private_key(private_path)
    public_key = security.load_public_key(public_path)
    secret = os.urandom(security.KEY_BYTES)
    plaintext = b"private job input\x00with binary bytes"

    envelope = security.sign_envelope(
        plaintext, private_key, purpose="manifest", encryption_key=secret
    )
    decoded, signer, fingerprint = security.verify_envelope(
        envelope,
        purpose="manifest",
        trusted_public_key=public_key,
        encryption_key=secret,
    )
    assert decoded == plaintext
    assert signer == public_key
    assert fingerprint == security.key_fingerprint(public_key)

    with pytest.raises(ValueError, match="authentication"):
        security.verify_envelope(
            envelope,
            purpose="manifest",
            trusted_public_key=public_key,
            encryption_key=os.urandom(security.KEY_BYTES),
        )
    with pytest.raises(ValueError, match="purpose"):
        security.verify_envelope(
            envelope,
            purpose="result",
            trusted_public_key=public_key,
            encryption_key=secret,
        )


def test_signature_tampering_and_unpinned_key_are_rejected(tmp_path):
    private_path, public_path, _ = security.generate_identity(tmp_path / "node.key")
    other_private_path, other_public_path, _ = security.generate_identity(tmp_path / "other.key")
    envelope = security.sign_envelope(
        b"signed", security.load_private_key(private_path), purpose="result", encryption_key=None
    )

    with pytest.raises(ValueError, match="unencrypted"):
        security.verify_envelope(
            envelope, purpose="result", trusted_public_key=security.load_public_key(public_path)
        )
    with pytest.raises(ValueError, match="untrusted signer"):
        security.verify_envelope(
            envelope,
            purpose="result",
            trusted_public_key=security.load_public_key(other_public_path),
            require_encryption=False,
        )

    tampered = envelope.replace(b"signed", b"forged")
    # The plaintext is base64 in the envelope; flip one payload character instead.
    tampered = envelope.replace(b"c2lnbmVk", b"Zm9yZ2Vk")
    with pytest.raises(ValueError, match="signature"):
        security.verify_envelope(
            tampered,
            purpose="result",
            trusted_public_key=security.load_public_key(public_path),
            require_encryption=False,
        )


def test_encryption_key_file_raw_and_hex(tmp_path):
    key = os.urandom(security.KEY_BYTES)
    path = tmp_path / "secret.bin"
    path.write_bytes(key)
    assert security.load_encryption_key(path) == key
    path.write_text(key.hex(), encoding="ascii")
    assert security.load_encryption_key(path) == key
    path.write_text("too short", encoding="ascii")
    with pytest.raises(ValueError, match="32"):
        security.load_encryption_key(path)
