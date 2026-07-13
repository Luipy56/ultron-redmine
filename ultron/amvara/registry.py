"""Amvara host registry: YAML allowlist + optional SSH config merge."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ultron.amvara.ssh_config import SshHostEntry, parse_ssh_config_hosts
from ultron.config import AmvaraConfig, AmvaraServerSpec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AmvaraHost:
    name: str
    ssh_target: str
    workspace: str
    description: str
    is_local: bool
    ssh_hostname: str | None = None
    ssh_user: str | None = None


@dataclass(frozen=True)
class AmvaraRegistry:
    local_host: str
    hosts: tuple[AmvaraHost, ...]
    prefer_agent: str
    fallback_enabled: bool
    timeout_seconds: float

    def get(self, host: str) -> AmvaraHost | None:
        key = host.strip().casefold()
        for h in self.hosts:
            if h.name == key:
                return h
        return None

    def list_allowed_hosts(self) -> tuple[str, ...]:
        return tuple(h.name for h in self.hosts)

    def validate_host(self, host: str) -> AmvaraHost:
        resolved = self.get(host)
        if resolved is None:
            allowed = ", ".join(self.list_allowed_hosts()) or "(none configured)"
            raise ValueError(
                f"Host {host!r} is not in the Amvara allowlist. Allowed: {allowed}."
            )
        return resolved


def _normalize_allowed(raw: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = str(item).strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return tuple(out)


def build_amvara_registry(cfg: AmvaraConfig) -> AmvaraRegistry:
    local_host = cfg.local_host.strip().casefold() or "amvara4"
    allowed = _normalize_allowed(cfg.allowed_hosts)
    if not allowed:
        logger.warning("amvara.allowed_hosts is empty — no Amvara audits will be permitted")

    ssh_entries: dict[str, SshHostEntry] = {}
    if cfg.merge_ssh_config:
        ssh_path = Path(cfg.ssh_config_path).expanduser() if cfg.ssh_config_path.strip() else Path.home() / ".ssh" / "config"
        ssh_entries = parse_ssh_config_hosts(ssh_path)
        if ssh_entries:
            logger.info(
                "Amvara SSH config: loaded %s host(s) from %s",
                len(ssh_entries),
                ssh_path,
            )

    specs_by_name: dict[str, AmvaraServerSpec] = {
        s.name.casefold(): s for s in cfg.servers if s.name.strip()
    }

    hosts: list[AmvaraHost] = []
    for name in allowed:
        spec = specs_by_name.get(name)
        ssh_entry = ssh_entries.get(name)
        ssh_target = (spec.ssh_target if spec else "") or name
        workspace = (spec.workspace if spec else "") or "/root"
        description = (spec.description if spec else "") or f"Amvara host {name}"
        hosts.append(
            AmvaraHost(
                name=name,
                ssh_target=ssh_target,
                workspace=workspace,
                description=description,
                is_local=name == local_host,
                ssh_hostname=ssh_entry.hostname if ssh_entry else None,
                ssh_user=ssh_entry.user if ssh_entry else None,
            )
        )

    return AmvaraRegistry(
        local_host=local_host,
        hosts=tuple(hosts),
        prefer_agent=cfg.audit.prefer_agent.strip().casefold() or "pi",
        fallback_enabled=cfg.audit.fallback_enabled,
        timeout_seconds=cfg.audit.timeout_seconds,
    )
