"""SSH preflight for Amvara remote audits: known_hosts + connectivity."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

from ultron.amvara.registry import AmvaraHost, AmvaraRegistry
from ultron.amvara.ssh_config import SshHostEntry, parse_ssh_config_hosts
from ultron.config import AmvaraConfig

logger = logging.getLogger(__name__)


def _ssh_config_path(cfg: AmvaraConfig) -> Path:
    if cfg.ssh_config_path.strip():
        return Path(cfg.ssh_config_path).expanduser()
    return Path.home() / ".ssh" / "config"


def _known_hosts_path() -> Path:
    return Path.home() / ".ssh" / "known_hosts"


def _ssh_entry_for_host(host: AmvaraHost, entries: dict[str, SshHostEntry]) -> SshHostEntry | None:
    return entries.get(host.name) or entries.get(host.ssh_target.casefold())


def _host_key_already_trusted(hostname: str, port: int, known_hosts: Path) -> bool:
    if not known_hosts.is_file():
        return False
    text = known_hosts.read_text(encoding="utf-8", errors="replace")
    if port == 22:
        return hostname in text
    bracket = f"[{hostname}]:{port}"
    return bracket in text or f"{hostname}:{port}" in text


def _run_ssh_keyscan(hostname: str, port: int) -> tuple[int, str, str]:
    cmd = ["ssh-keyscan", "-p", str(port), "-H", hostname]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _append_keyscan_lines(hostname: str, port: int, stdout: str) -> int:
    lines = [ln for ln in stdout.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        return 0
    known_hosts = _known_hosts_path()
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    block = "\n".join(lines) + "\n"
    with known_hosts.open("a", encoding="utf-8") as fp:
        fp.write(f"# ultron amvara preflight {hostname}:{port}\n")
        fp.write(block)
    return len(lines)


def _run_ssh_probe(ssh_target: str, *, timeout: int = 15) -> tuple[int, str, str]:
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"ConnectTimeout={timeout}",
        ssh_target,
        "true",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5, check=False)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def ensure_ssh_host_ready(
    host: AmvaraHost,
    *,
    amvara_cfg: AmvaraConfig,
    ssh_entries: dict[str, SshHostEntry] | None = None,
) -> str | None:
    """Ensure SSH to a remote Amvara host works. Return None on success, else error text."""
    if host.is_local:
        return None

    entries = ssh_entries if ssh_entries is not None else parse_ssh_config_hosts(_ssh_config_path(amvara_cfg))
    entry = _ssh_entry_for_host(host, entries)
    hostname = (entry.hostname if entry else None) or host.ssh_target
    port = entry.port if entry else 22

    code, _out, err = _run_ssh_probe(host.ssh_target)
    if code == 0:
        return None

    err_l = err.lower()
    if "host key verification failed" in err_l or "no ed25519 host key is known" in err_l:
        if not _host_key_already_trusted(hostname, port, _known_hosts_path()):
            logger.info("SSH preflight: keyscan %s:%s for %s", hostname, port, host.name)
            sc, scan_out, scan_err = _run_ssh_keyscan(hostname, port)
            if sc != 0 or not scan_out.strip():
                return (
                    f"SSH host key for **{host.name}** ({hostname}:{port}) is not trusted and "
                    f"`ssh-keyscan` failed. Check network/DNS and bot logs."
                )
            added = _append_keyscan_lines(hostname, port, scan_out)
            logger.info("SSH preflight: appended %s key line(s) for %s", added, host.name)
        code, _out, err = _run_ssh_probe(host.ssh_target)
        if code == 0:
            return None

    if "permission denied" in err_l:
        return (
            f"SSH authentication failed for **{host.name}** (`{host.ssh_target}`). "
            "Check `IdentityFile` in `~/.ssh/config` and that the Ultron service user can use that key."
        )
    if "connection refused" in err_l or "connection timed out" in err_l or "no route" in err_l:
        return (
            f"SSH cannot reach **{host.name}** (`{host.ssh_target}` → {hostname}:{port}). "
            "Check host/network/firewall."
        )

    detail = err.strip().splitlines()[-1] if err.strip() else f"exit {code}"
    return f"SSH preflight failed for **{host.name}** (`{host.ssh_target}`): {detail}"


async def ensure_ssh_host_ready_async(
    host: AmvaraHost,
    *,
    amvara_cfg: AmvaraConfig,
    ssh_entries: dict[str, SshHostEntry] | None = None,
) -> str | None:
    return await asyncio.to_thread(
        ensure_ssh_host_ready,
        host,
        amvara_cfg=amvara_cfg,
        ssh_entries=ssh_entries,
    )


def warm_ssh_known_hosts(registry: AmvaraRegistry, amvara_cfg: AmvaraConfig) -> int:
    """Trust SSH host keys for all allowlisted remote hosts. Returns count of hosts prepared."""
    entries = parse_ssh_config_hosts(_ssh_config_path(amvara_cfg))
    warmed = 0
    for h in registry.hosts:
        if h.is_local:
            continue
        err = ensure_ssh_host_ready(h, amvara_cfg=amvara_cfg, ssh_entries=entries)
        if err is None:
            warmed += 1
            logger.info("SSH preflight OK for %s", h.name)
        else:
            logger.warning("SSH preflight failed for %s: %s", h.name, err.replace("**", ""))
    return warmed
