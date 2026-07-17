"""Proof-bundle attestation — Ed25519 signatures over canonical bundle bytes.

A signature answers exactly one question: *has this bundle been altered since
the holder of this key signed it?* It does NOT prove the runs happened as
recorded — the producer computes everything in the bundle, so authenticity of
the *claims* still reduces to trusting the signer. What the signature buys is
tamper-evidence in transit and a stable identity to pin: a reviewer who has
your public key (obtained out of band) can reject a bundle that anyone else
edited or re-signed. That limit is stated in the bundle's not_proven section.

Mechanics:
  * The signed payload is the canonical JSON (sorted keys, compact, ascii) of
    the bundle WITHOUT its ``attestation`` field — so verification is
    deterministic and the signature can ride inside the document it signs.
  * Keys are Ed25519. The private key lives at
    ``~/.evarness/keys/proof_ed25519.pem`` (created on first ``--sign``,
    mode 0600, never leaves the machine); the public key is embedded in every
    attestation (base64 raw) and also written next to the private key as
    ``proof_ed25519.pub`` for out-of-band sharing.
  * Crypto comes from the ``cryptography`` package — the ``[sign]`` (or
    ``[secrets]``) extra. Signing/verifying without it installed is a clean
    error naming the extra, never a silent skip.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

ALGORITHM = "ed25519"


class AttestationError(ValueError):
    """Signing/verification could not be performed or failed."""


def _crypto():
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.hazmat.primitives import serialization
        return ed25519, serialization
    except ImportError:
        raise AttestationError(
            "attestation needs the 'cryptography' package — "
            'install it with:  pip install "evarness[sign]"')


def default_key_path() -> Path:
    return Path(os.environ.get(
        "EVARNESS_SIGNING_KEY",
        str(Path.home() / ".evarness" / "keys" / "proof_ed25519.pem")))


def canonical_bundle_bytes(proof: dict) -> bytes:
    """The signed payload: the bundle minus its attestation, canonical JSON."""
    doc = {k: v for k, v in proof.items() if k != "attestation"}
    return json.dumps(doc, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


def _load_or_create_key(key_path: Path):
    ed25519, serialization = _crypto()
    if key_path.is_file():
        key = serialization.load_pem_private_key(key_path.read_bytes(),
                                                 password=None)
        if not isinstance(key, ed25519.Ed25519PrivateKey):
            raise AttestationError(f"{key_path} is not an Ed25519 private key")
        return key, False
    key = ed25519.Ed25519PrivateKey.generate()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()))
    os.chmod(key_path, 0o600)
    key_path.with_suffix(".pub").write_text(public_key_b64(key) + "\n")
    return key, True


def public_key_b64(private_key) -> str:
    _, serialization = _crypto()
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def sign_proof(proof: dict, key_path: Path | str | None = None) -> dict:
    """Return the bundle with an ``attestation`` block. Creates the key on
    first use (and says so via the returned block's ``key_created`` flag)."""
    path = Path(key_path) if key_path else default_key_path()
    key, created = _load_or_create_key(path)
    signature = key.sign(canonical_bundle_bytes(proof))
    signed = dict(proof)
    signed["attestation"] = {
        "algorithm": ALGORITHM,
        "public_key": public_key_b64(key),
        "signature": base64.b64encode(signature).decode("ascii"),
        "key_path": str(path),
        "key_created": created,
    }
    return signed


def verify_attestation(proof: dict, pubkey_b64: str | None = None) -> dict:
    """Check the bundle's signature. Returns {ok, detail}. ``pubkey_b64`` pins
    the expected signer — without it the check only proves internal integrity
    (signed by whoever holds the embedded key)."""
    att = proof.get("attestation")
    if not att:
        return {"ok": False, "detail": "bundle is not signed"}
    if att.get("algorithm") != ALGORITHM:
        return {"ok": False, "detail": f"unsupported algorithm '{att.get('algorithm')}'"}
    if pubkey_b64 and att.get("public_key") != pubkey_b64:
        return {"ok": False, "detail": "embedded public key does not match the pinned key"}
    ed25519, _ = _crypto()
    try:
        key = ed25519.Ed25519PublicKey.from_public_bytes(
            base64.b64decode(att["public_key"]))
        key.verify(base64.b64decode(att["signature"]),
                   canonical_bundle_bytes(proof))
    except Exception:
        return {"ok": False, "detail": "signature does not match the bundle contents"}
    pinned = " (pinned key)" if pubkey_b64 else " (embedded key — pin one to identify the signer)"
    return {"ok": True, "detail": f"ed25519 signature valid{pinned}"}
