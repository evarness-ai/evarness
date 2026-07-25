"""Proof-bundle hardening (D58) — hash chain, subject completeness, offline
verification, Ed25519 attestation, tamper detection."""

import copy
import json

from evarness.domains.agents import patterns
from evarness.core.attest import sign_proof, verify_attestation
from evarness.core.prove import prove, verify_proof
from evarness.core.graph import GraphModel
from evarness.core.trace import chain_digest

FLAGSHIP = "governed_email_assistant"


def _proof(**kw):
    graph = GraphModel.model_validate(patterns.load_pattern(FLAGSHIP))
    scenarios = [(n, patterns.fixture_text(FLAGSHIP, n)) for n in patterns.fixture_names(FLAGSHIP)]
    return prove(graph, scenarios, pattern_id=FLAGSHIP, **kw)


# -------------------------------------------------------------------- chain


def test_chain_commits_to_every_prefix():
    events = [
        {"seq": i, "ts": 1.0, "node_id": None, "type": "e", "payload": {"i": i}} for i in range(5)
    ]
    full = chain_digest(events)
    assert full.startswith("c1:chain-sha256:")
    assert chain_digest(events[:-1]) != full  # truncation
    swapped = [events[1], events[0]] + events[2:]
    assert chain_digest(swapped) != full  # reorder
    edited = copy.deepcopy(events)
    edited[2]["payload"]["i"] = 99
    assert chain_digest(edited) != full  # edit
    assert chain_digest(events) == full  # reproducible


# ------------------------------------------------------- subject completeness


def test_bundle_pins_tools_contracts_and_environment():
    proof = _proof()
    assert proof["proof_version"] == "p2"
    s = proof["subject"]
    assert s["tools"]["email.search"].startswith("sha256:")  # manifest pinned
    assert s["invariant_defs_sha256"].startswith("sha256:")  # content, not ids
    env = proof["environment"]
    assert env["python"] and env["platform"]
    assert env["dependencies_sha256"].startswith("sha256:")
    for sc in proof["scenarios"]:
        assert sc["event_chain"].startswith("c1:chain-sha256:")
    assert any("producer honesty" in n for n in proof["not_proven"])


def test_invariant_defs_hash_pins_content_not_ids():
    a = _proof(
        invariant_defs={"no-model-call-after-block": {"assert": {"never": {"type": "llm_request"}}}}
    )
    b = _proof()
    assert a["subject"]["invariant_defs_sha256"] != b["subject"]["invariant_defs_sha256"]


# ------------------------------------------------------------------- verify


def test_verify_passes_a_fresh_bundle_and_catches_tampering():
    proof = _proof()
    ok = verify_proof(proof)
    assert ok["ok"]
    assert all(c["ok"] in (True, None) for c in ok["checks"])

    tampered = copy.deepcopy(proof)
    tampered["scenarios"][0]["events"][3]["payload"] = {"forged": True}
    bad = verify_proof(tampered)
    assert not bad["ok"]
    failed = {c["check"] for c in bad["checks"] if c["ok"] is False}
    assert {"digest recomputes", "event chain recomputes"} <= failed

    lied = copy.deepcopy(proof)
    lied["verdict"]["ok"] = True
    lied["verdict"]["invariants_pass"] = True
    lied["scenarios"][0]["invariants"] = {"passed": 0, "failed": 1, "results": []}
    assert not verify_proof(lied)["ok"]  # verdict vs rows


def test_verify_no_events_bundle_is_honest_not_silent():
    result = verify_proof(_proof(include_events=False))
    digests = [c for c in result["checks"] if c["check"] == "digest recomputes"]
    assert digests and all(c["ok"] is None for c in digests)
    assert "--no-events" in digests[0]["detail"]
    assert result["ok"]  # skipped ≠ failed


def test_verify_unsigned_vs_required_signature():
    proof = _proof()
    assert verify_proof(proof)["ok"]  # unsigned tolerated
    strict = verify_proof(proof, require_signature=True)
    assert not strict["ok"]
    sig = next(c for c in strict["checks"] if c["check"] == "signature")
    assert sig["ok"] is False and "unsigned" in sig["detail"]


# -------------------------------------------------------------- attestation


def test_sign_and_verify_roundtrip(tmp_path):
    key = tmp_path / "keys" / "proof_ed25519.pem"
    signed = sign_proof(_proof(), key_path=key)
    att = signed["attestation"]
    assert att["algorithm"] == "ed25519" and att["key_created"] is True
    assert key.is_file() and key.with_suffix(".pub").is_file()
    assert oct(key.stat().st_mode & 0o777) == "0o600"

    assert verify_attestation(signed)["ok"]
    assert verify_proof(signed, require_signature=True)["ok"]
    # pinning: the right key passes, a wrong pin fails
    pub = key.with_suffix(".pub").read_text().strip()
    assert verify_attestation(signed, pubkey_b64=pub)["ok"]
    assert not verify_attestation(signed, pubkey_b64="AAAA")["ok"]
    # second sign reuses the key
    again = sign_proof(_proof(), key_path=key)
    assert again["attestation"]["key_created"] is False
    assert again["attestation"]["public_key"] == att["public_key"]


def test_signature_breaks_on_any_bundle_edit(tmp_path):
    signed = sign_proof(_proof(), key_path=tmp_path / "k.pem")
    forged = copy.deepcopy(signed)
    forged["verdict"]["ok"] = "definitely"
    check = verify_attestation(forged)
    assert not check["ok"] and "does not match" in check["detail"]
    assert not verify_proof(forged, require_signature=True)["ok"]


# ------------------------------------------------------------------- CLI


def test_cli_prove_sign_then_verify(tmp_path, capsys, monkeypatch):
    from evarness.cli import main

    monkeypatch.chdir(tmp_path)
    key = tmp_path / "sign.pem"
    assert main(["prove", FLAGSHIP, "-o", "p.json", "--sign", "--key", str(key)]) == 0
    out = capsys.readouterr().out
    assert "new signing key created" in out

    assert main(["verify", "p.json", "--require-signature"]) == 0
    out = capsys.readouterr().out
    assert "VERIFY: OK" in out and "✓ signature" in out

    # pin via the .pub file path
    assert main(["verify", "p.json", "--pubkey", str(key.with_suffix(".pub"))]) == 0
    capsys.readouterr()

    # tamper -> nonzero exit and the failing checks named
    doc = json.loads((tmp_path / "p.json").read_text())
    doc["scenarios"][0]["events"][0]["payload"]["input"] = "forged"
    (tmp_path / "p.json").write_text(json.dumps(doc))
    assert main(["verify", "p.json"]) == 1
    out = capsys.readouterr().out
    assert "VERIFY: FAILED" in out and "✗" in out
