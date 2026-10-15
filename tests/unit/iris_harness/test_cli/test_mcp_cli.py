"""iris mcp keygen|sign|verify CLI — Phase 6 sub-phase 6b.3."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from iris_harness.cli.mcp import mcp_signing_app as mcp_app

runner = CliRunner()


def _write_servers(path: Path, *, signed: dict[str, str] | None = None) -> None:
    server: dict[str, object] = {
        "name": "remote",
        "enabled": True,
        "transport": "http",
        "url": "http://127.0.0.1:8787/mcp",
    }
    if signed:
        server.update(signed)
    path.write_text(
        yaml.safe_dump({"version": "1", "enabled": True, "servers": [server]}), encoding="utf-8"
    )


def _trust(path: Path) -> Path:
    return path


def test_keygen_writes_private_key_and_trust_entry(tmp_path: Path) -> None:
    trust = tmp_path / "trust.yaml"
    keys_dir = tmp_path / "keys"
    result = runner.invoke(
        mcp_app,
        ["keygen", "--id", "ops", "--trust-store", str(trust)],
        env={"IRIS_MCP_KEYS_DIR": str(keys_dir)},
    )
    assert result.exit_code == 0, result.output
    key_file = keys_dir / "ops.key"
    assert key_file.exists()
    assert oct(key_file.stat().st_mode)[-3:] == "600"
    doc = yaml.safe_load(trust.read_text())
    assert doc["keys"][0]["key_id"] == "ops"
    assert doc["keys"][0]["public_key"]


def test_keygen_rejects_duplicate_id(tmp_path: Path) -> None:
    trust = tmp_path / "trust.yaml"
    keys_dir = tmp_path / "keys"
    env = {"IRIS_MCP_KEYS_DIR": str(keys_dir)}
    runner.invoke(mcp_app, ["keygen", "--id", "ops", "--trust-store", str(trust)], env=env)
    second = runner.invoke(mcp_app, ["keygen", "--id", "ops", "--trust-store", str(trust)], env=env)
    assert second.exit_code == 1


def test_sign_then_verify_roundtrip(tmp_path: Path) -> None:
    trust = tmp_path / "trust.yaml"
    keys_dir = tmp_path / "keys"
    servers = tmp_path / "mcp-servers.yaml"
    env = {"IRIS_MCP_KEYS_DIR": str(keys_dir)}
    _write_servers(servers)

    assert (
        runner.invoke(
            mcp_app, ["keygen", "--id", "ops", "--trust-store", str(trust)], env=env
        ).exit_code
        == 0
    )
    sign_res = runner.invoke(
        mcp_app, ["sign", "remote", "--key", "ops", "--servers", str(servers)], env=env
    )
    assert sign_res.exit_code == 0, sign_res.output

    doc = yaml.safe_load(servers.read_text())
    assert doc["servers"][0]["signature"]
    assert doc["servers"][0]["signed_by"] == "ops"

    verify_res = runner.invoke(
        mcp_app,
        ["verify", "remote", "--servers", str(servers), "--trust-store", str(trust)],
    )
    assert verify_res.exit_code == 0, verify_res.output
    assert "verified" in verify_res.output


def test_verify_unsigned_exits_nonzero(tmp_path: Path) -> None:
    servers = tmp_path / "mcp-servers.yaml"
    trust = tmp_path / "trust.yaml"
    trust.write_text("version: 1\nkeys: []\n", encoding="utf-8")
    _write_servers(servers)
    result = runner.invoke(
        mcp_app, ["verify", "--servers", str(servers), "--trust-store", str(trust)]
    )
    assert result.exit_code == 1
    assert "unsigned" in result.output


def test_sign_unknown_server_errors(tmp_path: Path) -> None:
    servers = tmp_path / "mcp-servers.yaml"
    keys_dir = tmp_path / "keys"
    trust = tmp_path / "trust.yaml"
    env = {"IRIS_MCP_KEYS_DIR": str(keys_dir)}
    _write_servers(servers)
    runner.invoke(mcp_app, ["keygen", "--id", "ops", "--trust-store", str(trust)], env=env)
    result = runner.invoke(
        mcp_app, ["sign", "ghost", "--key", "ops", "--servers", str(servers)], env=env
    )
    assert result.exit_code == 1


def test_verify_detects_tampering_after_signing(tmp_path: Path) -> None:
    trust = tmp_path / "trust.yaml"
    keys_dir = tmp_path / "keys"
    servers = tmp_path / "mcp-servers.yaml"
    env = {"IRIS_MCP_KEYS_DIR": str(keys_dir)}
    _write_servers(servers)
    runner.invoke(mcp_app, ["keygen", "--id", "ops", "--trust-store", str(trust)], env=env)
    runner.invoke(mcp_app, ["sign", "remote", "--key", "ops", "--servers", str(servers)], env=env)
    # Tamper with the signed config: swap the URL.
    doc = yaml.safe_load(servers.read_text())
    doc["servers"][0]["url"] = "http://evil.test/mcp"
    servers.write_text(yaml.safe_dump(doc), encoding="utf-8")

    result = runner.invoke(
        mcp_app, ["verify", "--servers", str(servers), "--trust-store", str(trust)]
    )
    assert result.exit_code == 1
    assert "invalid" in result.output


# --- list / revoke / remove ---------------------------------------------------------------


def _signed_setup(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    trust = tmp_path / "trust.yaml"
    servers = tmp_path / "mcp-servers.yaml"
    env = {"IRIS_MCP_KEYS_DIR": str(tmp_path / "keys")}
    _write_servers(servers)
    runner.invoke(mcp_app, ["keygen", "--id", "ops", "--trust-store", str(trust)], env=env)
    runner.invoke(mcp_app, ["sign", "remote", "--key", "ops", "--servers", str(servers)], env=env)
    return servers, trust, env


def _verify_exit(servers: Path, trust: Path) -> int:
    return runner.invoke(
        mcp_app, ["verify", "--servers", str(servers), "--trust-store", str(trust)]
    ).exit_code


def test_list_shows_the_servers_with_their_verdicts_and_the_keys(tmp_path: Path) -> None:
    servers, trust, _ = _signed_setup(tmp_path)

    result = runner.invoke(
        mcp_app, ["list", "--servers", str(servers), "--trust-store", str(trust)]
    )

    assert result.exit_code == 0, result.output
    assert "remote" in result.output and "verified" in result.output
    assert "ops" in result.output and "trusted" in result.output


def test_revoke_a_key_makes_what_it_signed_unverified(tmp_path: Path) -> None:
    servers, trust, _ = _signed_setup(tmp_path)
    assert _verify_exit(servers, trust) == 0

    result = runner.invoke(
        mcp_app, ["revoke", "ops", "--trust-store", str(trust), "--servers", str(servers)]
    )

    assert result.exit_code == 0, result.output
    assert "remote" in result.output  # says which server is now refused
    doc = yaml.safe_load(trust.read_text())
    assert doc["revoked_key_ids"] == ["ops"]
    assert [k["key_id"] for k in doc["keys"]] == ["ops"]  # the key stays on record
    assert _verify_exit(servers, trust) == 1
    listed = runner.invoke(
        mcp_app, ["list", "--servers", str(servers), "--trust-store", str(trust)]
    )
    assert "revoked" in listed.output


def test_revoke_is_idempotent_and_refuses_an_unknown_key(tmp_path: Path) -> None:
    servers, trust, _ = _signed_setup(tmp_path)
    args = ["--trust-store", str(trust), "--servers", str(servers)]

    runner.invoke(mcp_app, ["revoke", "ops", *args])
    again = runner.invoke(mcp_app, ["revoke", "ops", *args])
    ghost = runner.invoke(mcp_app, ["revoke", "ghost", *args])

    assert again.exit_code == 0 and "already revoked" in again.output
    assert yaml.safe_load(trust.read_text())["revoked_key_ids"] == ["ops"]
    assert ghost.exit_code == 1


def test_revoke_one_signature_keeps_the_key_trusted(tmp_path: Path) -> None:
    servers, trust, _ = _signed_setup(tmp_path)
    signature = yaml.safe_load(servers.read_text())["servers"][0]["signature"]

    result = runner.invoke(
        mcp_app,
        [
            "revoke",
            "--signature",
            signature,
            "--trust-store",
            str(trust),
            "--servers",
            str(servers),
        ],
    )

    assert result.exit_code == 0, result.output
    doc = yaml.safe_load(trust.read_text())
    assert doc["revoked_signatures"] == [signature]
    assert not doc.get("revoked_key_ids")
    assert _verify_exit(servers, trust) == 1


def test_revoke_wants_exactly_one_of_a_key_or_a_signature(tmp_path: Path) -> None:
    servers, trust, _ = _signed_setup(tmp_path)
    args = ["--trust-store", str(trust), "--servers", str(servers)]

    assert runner.invoke(mcp_app, ["revoke", *args]).exit_code == 2
    assert runner.invoke(mcp_app, ["revoke", "ops", "--signature", "x", *args]).exit_code == 2


def test_remove_deletes_the_entry_and_says_the_key_is_still_trusted(tmp_path: Path) -> None:
    servers, trust, _ = _signed_setup(tmp_path)

    result = runner.invoke(
        mcp_app, ["remove", "remote", "--servers", str(servers), "--trust-store", str(trust)]
    )

    assert result.exit_code == 0, result.output
    assert yaml.safe_load(servers.read_text())["servers"] == []
    assert "still trusted" in result.output and "iris mcp revoke ops" in result.output
    assert not yaml.safe_load(trust.read_text()).get("revoked_key_ids")


def test_remove_with_revoke_signature_stops_the_same_entry_coming_back(tmp_path: Path) -> None:
    servers, trust, env = _signed_setup(tmp_path)
    signed_entry = yaml.safe_load(servers.read_text())["servers"][0]

    result = runner.invoke(
        mcp_app,
        [
            "remove",
            "remote",
            "--revoke-signature",
            "--servers",
            str(servers),
            "--trust-store",
            str(trust),
        ],
    )
    assert result.exit_code == 0, result.output
    assert yaml.safe_load(trust.read_text())["revoked_signatures"] == [signed_entry["signature"]]

    doc = yaml.safe_load(servers.read_text())
    doc["servers"].append(signed_entry)  # pasted back in
    servers.write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert _verify_exit(servers, trust) == 1


def test_remove_unknown_server_and_unsigned_revoke_are_errors(tmp_path: Path) -> None:
    servers = tmp_path / "mcp-servers.yaml"
    _write_servers(servers)

    ghost = runner.invoke(mcp_app, ["remove", "ghost", "--servers", str(servers)])
    unsigned = runner.invoke(
        mcp_app, ["remove", "remote", "--revoke-signature", "--servers", str(servers)]
    )

    assert ghost.exit_code == 1
    assert unsigned.exit_code == 1
    assert len(yaml.safe_load(servers.read_text())["servers"]) == 1  # nothing was removed


# --- launchers that name no script file (#287) --------------------------------------------


def _stdio_servers(path: Path, command: str, *args: str) -> None:
    server = {
        "name": "remote",
        "enabled": True,
        "transport": "stdio",
        "command": command,
        "args": list(args),
    }
    path.write_text(
        yaml.safe_dump({"version": "1", "enabled": True, "servers": [server]}), encoding="utf-8"
    )


def _keygen(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    trust = tmp_path / "trust.yaml"
    env = {"IRIS_MCP_KEYS_DIR": str(tmp_path / "keys")}
    runner.invoke(mcp_app, ["keygen", "--id", "ops", "--trust-store", str(trust)], env=env)
    return trust, env


def test_sign_refuses_a_launcher_that_does_not_pin_its_release(tmp_path: Path) -> None:
    servers = tmp_path / "mcp-servers.yaml"
    _stdio_servers(servers, "npx", "-y", "some-mcp-server")
    _, env = _keygen(tmp_path)

    result = runner.invoke(
        mcp_app, ["sign", "remote", "--key", "ops", "--servers", str(servers)], env=env
    )

    assert result.exit_code == 1
    assert "exact version" in result.output and "pkg@1.2.3" in result.output
    assert "signature" not in yaml.safe_load(servers.read_text())["servers"][0]


def test_sign_then_verify_a_launcher_that_pins_its_release(tmp_path: Path) -> None:
    servers = tmp_path / "mcp-servers.yaml"
    _stdio_servers(servers, "npx", "-y", "some-mcp-server@1.2.3")
    trust, env = _keygen(tmp_path)

    signed = runner.invoke(
        mcp_app, ["sign", "remote", "--key", "ops", "--servers", str(servers)], env=env
    )
    verified = runner.invoke(
        mcp_app, ["verify", "--servers", str(servers), "--trust-store", str(trust)]
    )

    assert signed.exit_code == 0, signed.output
    assert verified.exit_code == 0, verified.output


def test_verify_and_list_report_a_signed_unpinned_launcher_as_invalid(tmp_path: Path) -> None:
    servers = tmp_path / "mcp-servers.yaml"
    _stdio_servers(servers, "npx", "-y", "some-mcp-server@1.2.3")
    trust, env = _keygen(tmp_path)
    runner.invoke(mcp_app, ["sign", "remote", "--key", "ops", "--servers", str(servers)], env=env)
    doc = yaml.safe_load(servers.read_text())
    doc["servers"][0]["args"] = ["-y", "some-mcp-server"]  # the pin dropped after signing
    servers.write_text(yaml.safe_dump(doc), encoding="utf-8")

    verify = runner.invoke(
        mcp_app, ["verify", "--servers", str(servers), "--trust-store", str(trust)]
    )
    listed = runner.invoke(
        mcp_app, ["list", "--servers", str(servers), "--trust-store", str(trust)]
    )

    assert verify.exit_code == 1 and "invalid" in verify.output
    assert "invalid" in listed.output
