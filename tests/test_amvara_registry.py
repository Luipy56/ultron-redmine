from __future__ import annotations

from pathlib import Path

from ultron.amvara.registry import build_amvara_registry
from ultron.amvara.ssh_config import parse_ssh_config_hosts
from ultron.config import AmvaraConfig, AmvaraServerSpec


def test_parse_ssh_config_amvara_hosts(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    cfg.write_text(
        "\n".join(
            [
                "Host amvara3",
                "    HostName 10.0.0.3",
                "    Port 60022",
                "    User root",
                "",
                "Host other",
                "    HostName 1.2.3.4",
            ]
        ),
        encoding="utf-8",
    )
    entries = parse_ssh_config_hosts(cfg)
    assert "amvara3" in entries
    assert entries["amvara3"].hostname == "10.0.0.3"
    assert entries["amvara3"].port == 60022
    assert "other" not in entries


def test_registry_local_host() -> None:
    reg = build_amvara_registry(
        AmvaraConfig(
            local_host="amvara4",
            allowed_hosts=("amvara3", "amvara4"),
            merge_ssh_config=False,
        )
    )
    h3 = reg.get("amvara3")
    h4 = reg.get("amvara4")
    assert h3 is not None and not h3.is_local
    assert h4 is not None and h4.is_local


def test_registry_validate_rejects_unknown() -> None:
    reg = build_amvara_registry(
        AmvaraConfig(allowed_hosts=("amvara3",), merge_ssh_config=False)
    )
    try:
        reg.validate_host("amvara99")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "allowlist" in str(e).lower()


def test_registry_server_override() -> None:
    reg = build_amvara_registry(
        AmvaraConfig(
            allowed_hosts=("amvara3",),
            servers=(AmvaraServerSpec(name="amvara3", workspace="/opt", description="ops"),),
            merge_ssh_config=False,
        )
    )
    h = reg.get("amvara3")
    assert h is not None
    assert h.workspace == "/opt"
