"""Resolve runtime settings for ``/pi`` from config, env, and ``llm_chain``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ultron.config import AppConfig, LLMProviderSpec
from ultron.ollama_slash import is_ollama_like_spec, resolve_ol_provider_index


@dataclass(frozen=True)
class PiRunSettings:
    repo_root: Path
    workspace: Path
    state_dir: Path
    ollama_base_url: str
    model: str
    provider: str
    api_key: str
    timeout_seconds: float
    bin_path: Path
    config_dir: Path
    prompt_path: Path | None
    tunnel_script: Path | None
    ollama_connect_timeout_seconds: float
    ollama_connect_retries: int
    ollama_connect_retry_delay_seconds: float
    ollama_busy_check: bool
    ollama_busy_if_models_loaded: bool
    ollama_inference_probe_seconds: float


def default_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_pi_bin(*, repo_root: Path, bin_path_cfg: str) -> Path:
    env_bin = os.environ.get("ULTRON_PI_BIN", "").strip()
    if env_bin:
        p = Path(env_bin).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return p.resolve()
        raise RuntimeError(f"ULTRON_PI_BIN is not executable: {p}")

    if bin_path_cfg.strip():
        p = Path(bin_path_cfg).expanduser()
        if not p.is_absolute():
            p = repo_root / p
        if p.is_file() and os.access(p, os.X_OK):
            return p.resolve()
        raise RuntimeError(f"pi.bin_path is not executable: {p}")

    local = repo_root / "node_modules" / ".bin" / "pi"
    if local.is_file() and os.access(local, os.X_OK):
        return local.resolve()

    raise RuntimeError(
        "pi binary not found. Run `npm install --ignore-scripts` in the Ultron checkout "
        "(installs @earendil-works/pi-coding-agent) or set ULTRON_PI_BIN / pi.bin_path in config.yaml."
    )


def _resolve_path_under_repo(repo_root: Path, raw: str, *, default: Path) -> Path:
    if not raw.strip():
        return default.resolve()
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = repo_root / p
    return p.resolve()


def _ollama_chain_entry(chain: tuple[LLMProviderSpec, ...]) -> LLMProviderSpec:
    idx = resolve_ol_provider_index(chain, None)
    return chain[idx]


def resolve_ollama_endpoint(app_cfg: AppConfig) -> tuple[str, str] | None:
    """Return ``(base_url, model)`` for the first Ollama-like ``llm_chain`` entry, or ``None``."""
    chain = app_cfg.llm_chain
    if chain is None:
        return None
    if not any(is_ollama_like_spec(s) for s in chain):
        return None
    pi_cfg = app_cfg.pi
    ollama_spec = _ollama_chain_entry(chain)
    base = pi_cfg.ollama_base_url.strip() or ollama_spec.base_url
    model = pi_cfg.model.strip() or ollama_spec.model
    return base, model


def pi_availability_message(app_cfg: AppConfig, *, repo_root: Path | None = None) -> str | None:
    """Return a user-facing reason when `/pi` is unavailable, or ``None`` if OK."""
    root = (repo_root or default_repo_root()).resolve()
    pi_cfg = app_cfg.pi

    if pi_cfg.enabled is False:
        return (
            "**`/pi`** is disabled. Set **`pi.enabled: true`** in `config.yaml` and install "
            "`@earendil-works/pi-coding-agent` (`npm install` in the checkout)."
        )

    chain = app_cfg.llm_chain
    if chain is None:
        return (
            "**`/pi`** needs an Ollama entry in **`llm_chain`**. Add a provider with "
            "`base_url` like `http://127.0.0.1:11434/v1` and run `npm install` for pi."
        )

    if not any(is_ollama_like_spec(s) for s in chain):
        return (
            "**`/pi`** needs an Ollama-like **`llm_chain`** entry (port **11434** or name containing "
            "`ollama`). Cloud-only chains cannot run pi locally."
        )

    try:
        resolve_pi_bin(repo_root=root, bin_path_cfg=pi_cfg.bin_path)
    except RuntimeError as e:
        return f"**`/pi`** is not ready: {e}"

    return None


def pi_is_available(app_cfg: AppConfig, *, repo_root: Path | None = None) -> bool:
    return pi_availability_message(app_cfg, repo_root=repo_root) is None


def build_pi_run_settings(
    app_cfg: AppConfig,
    *,
    state_dir: Path,
    repo_root: Path | None = None,
) -> PiRunSettings:
    root = (repo_root or default_repo_root()).resolve()
    pi_cfg = app_cfg.pi
    reason = pi_availability_message(app_cfg, repo_root=root)
    if reason is not None:
        raise RuntimeError(reason.replace("**", ""))

    chain = app_cfg.llm_chain
    assert chain is not None
    ollama_spec = _ollama_chain_entry(chain)

    ollama_base = pi_cfg.ollama_base_url.strip() or ollama_spec.base_url
    model = pi_cfg.model.strip() or ollama_spec.model
    api_key = pi_cfg.api_key.strip() or os.environ.get(ollama_spec.api_key_env, "").strip() or "ollama"

    workspace = _resolve_path_under_repo(root, pi_cfg.workspace, default=root)
    config_dir = _resolve_path_under_repo(
        root,
        pi_cfg.config_dir,
        default=root / ".pi" / "agent",
    )
    bin_path = resolve_pi_bin(repo_root=root, bin_path_cfg=pi_cfg.bin_path)

    prompt_path: Path | None = None
    if pi_cfg.prompt_path.strip():
        prompt_path = Path(pi_cfg.prompt_path).expanduser()
        if not prompt_path.is_file():
            raise RuntimeError(f"pi.prompt_path not found: {prompt_path}")

    tunnel_raw = os.environ.get("ULTRON_OLLAMA_TUNNEL_SCRIPT", "").strip() or pi_cfg.tunnel_script.strip()
    tunnel_script = Path(tunnel_raw).expanduser() if tunnel_raw else None

    return PiRunSettings(
        repo_root=root,
        workspace=workspace,
        state_dir=state_dir.resolve(),
        ollama_base_url=ollama_base,
        model=model,
        provider=pi_cfg.provider.strip() or "ollama",
        api_key=api_key,
        timeout_seconds=pi_cfg.timeout_seconds,
        bin_path=bin_path,
        config_dir=config_dir,
        prompt_path=prompt_path,
        tunnel_script=tunnel_script,
        ollama_connect_timeout_seconds=pi_cfg.ollama_connect_timeout_seconds,
        ollama_connect_retries=pi_cfg.ollama_connect_retries,
        ollama_connect_retry_delay_seconds=pi_cfg.ollama_connect_retry_delay_seconds,
        ollama_busy_check=pi_cfg.ollama_busy_check,
        ollama_busy_if_models_loaded=pi_cfg.ollama_busy_if_models_loaded,
        ollama_inference_probe_seconds=pi_cfg.ollama_inference_probe_seconds,
    )
