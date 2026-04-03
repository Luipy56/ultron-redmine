from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from ultron.config import LLMProviderResolved

logger = logging.getLogger(__name__)

# Log lines: never dump multi‑KB HTML bodies from mistaken API URLs.
_SAFE_EXC_MAX = 400
_HTML_HINT = re.compile(r"<!doctype|<\s*html\b", re.IGNORECASE)


def safe_exc_message(exc: BaseException, *, max_len: int = _SAFE_EXC_MAX) -> str:
    """Short string for logging; strips or truncates HTML error pages and long bodies."""
    s = str(exc).strip()
    if not s:
        return type(exc).__name__
    head = s[:800]
    if _HTML_HINT.search(head) or (head.lstrip().startswith("<") and "<body" in head.lower()):
        return (
            f"{type(exc).__name__}: [body omitted; response looks like HTML, {len(s)} chars]"
        )
    if len(s) > max_len:
        return f"{s[:max_len]}... [truncated, {len(s)} chars total]"
    return s


class NoLLMConfiguredError(RuntimeError):
    """Raised when a feature needs a language model but none is configured (``NullLLMBackend``)."""


class LLMChainExhaustedError(Exception):
    """Every entry in ``llm_chain`` failed with an error that allows trying the next provider."""

    def __init__(self, *, provider_count: int, last_error: BaseException) -> None:
        self.provider_count = provider_count
        self.last_error = last_error
        super().__init__(
            f"All {provider_count} llm_chain providers failed (last: {type(last_error).__name__})."
        )


def format_llm_endpoint(base_url: str) -> str:
    """Short label for logs: host plus path when not only ``/`` (e.g. ``api.openai.com/v1``)."""
    u = urlparse(base_url.strip().rstrip("/"))
    host = u.netloc or "?"
    path = (u.path or "").rstrip("/")
    if not path or path == "/":
        return host
    return f"{host}{path}"


class LLMBackend(Protocol):
    """Anything Ultron uses for chat completions (single provider or chain)."""

    @property
    def model(self) -> str: ...

    async def complete(self, *, system: str, user: str) -> str: ...


@dataclass(frozen=True)
class NullLLMBackend:
    """Placeholder when no language model is configured; ``complete`` must not be used for real work."""

    @property
    def model(self) -> str:
        return "(none)"

    async def complete(self, *, system: str, user: str) -> str:
        raise NoLLMConfiguredError(
            "No language model is configured. Set LLM_* in the environment or define llm_chain in config.yaml."
        )


def _should_fallback_to_next_provider(exc: BaseException) -> bool:
    """Try the next llm_chain entry on transport failures and any HTTP error from the API.

    Wrong base_url, TLS, DNS, timeouts, invalid keys (401), bad model (400), 5xx, etc. all
    advance the chain so a misconfigured slot does not block the rest.
    """
    if isinstance(exc, APIConnectionError):
        return True
    if isinstance(exc, APIStatusError):
        return True
    return False


def _why_switching_to_next_llm(exc: BaseException) -> str:
    """Human-readable policy line for logs when the chain advances to the next entry."""
    if isinstance(exc, APITimeoutError):
        return (
            "policy: request timed out (provider too slow or hung) — trying next llm_chain entry "
            "(TCP client is closed when possible so local servers e.g. Ollama can stop)"
        )
    if isinstance(exc, APIConnectionError):
        return (
            "policy: could not reach provider (wrong URL/host/port, TLS, DNS, refused connection) — "
            "trying next llm_chain entry"
        )
    if isinstance(exc, APIStatusError):
        code = exc.status_code
        if code == 401:
            return (
                "policy: HTTP 401 (invalid, missing, or wrong API key for this base_url) — "
                "trying next llm_chain entry"
            )
        if code == 403:
            return (
                "policy: HTTP 403 (forbidden, plan, or permission) — trying next llm_chain entry"
            )
        if code == 404:
            return (
                "policy: HTTP 404 (wrong path or resource; check base_url and model id) — "
                "trying next llm_chain entry"
            )
        if code == 400:
            return (
                "policy: HTTP 400 (bad request; often wrong model name or parameters) — "
                "trying next llm_chain entry"
            )
        if code == 429:
            return "policy: HTTP 429 (rate limit or quota) — trying next llm_chain entry"
        if code >= 500:
            return (
                f"policy: HTTP {code} server/upstream error — trying next llm_chain entry"
            )
        return f"policy: HTTP {code} from provider — trying next llm_chain entry"
    return "policy: unexpected error class for llm_chain fallback; trying next llm_chain entry"


def _why_not_switching_llm(exc: BaseException) -> str:
    """Human-readable line when the chain stops on this entry (no further provider)."""
    return (
        f"policy: {type(exc).__name__} is not covered by llm_chain fallback "
        f"(only provider HTTP errors and connection/timeout errors advance the chain); detail: {safe_exc_message(exc)}"
    )


def _why_last_chain_entry_failed(exc: BaseException) -> str:
    """When the final entry fails with a transient-classified error."""
    base = _why_switching_to_next_llm(exc)
    return f"{base.rstrip()} — but this was the last llm_chain entry, so the request fails"


def chain_skip_user_reason(exc: BaseException) -> str:
    """Short, user-facing reason for Discord when advancing to the next llm_chain entry."""
    if isinstance(exc, APITimeoutError):
        return "timed out"
    if isinstance(exc, APIConnectionError):
        return "could not connect"
    if isinstance(exc, APIStatusError):
        code = exc.status_code
        if code == 401:
            return "invalid API key"
        if code == 403:
            return "forbidden"
        if code == 404:
            return "not found"
        if code == 400:
            return "bad request"
        if code == 429:
            return "rate limit or quota"
        if code >= 500:
            return f"server error ({code})"
        return f"HTTP {code}"
    return type(exc).__name__


@dataclass(frozen=True)
class ChainSkipNotice:
    """Emitted when the chain skips from one provider to the next (for UI updates)."""

    from_entry: str
    from_model: str
    to_entry: str
    to_model: str
    reason: str


ChainSkipCallback = Callable[[ChainSkipNotice], Awaitable[None]]


@dataclass
class LLMClient:
    base_url: str
    api_key: str
    model: str
    timeout: float = 120.0
    max_retries: int = 2
    _sdk_client: AsyncOpenAI | None = field(default=None, repr=False)

    def _sdk(self) -> AsyncOpenAI:
        if self._sdk_client is None:
            self._sdk_client = AsyncOpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=self.timeout,
                max_retries=self.max_retries,
            )
        return self._sdk_client

    async def shutdown_sdk_client(self) -> None:
        """Close the OpenAI SDK HTTP client and clear the cache.

        Use after a timeout or connection failure so the TCP connection drops; local
        servers (e.g. Ollama) can then abort in-flight generation instead of
        serving a client that has already moved on.
        """
        sdk = self._sdk_client
        if sdk is None:
            return
        try:
            await sdk.close()
        except Exception as ex:
            logger.warning(
                "LLM HTTP client close failed | model=%r | endpoint=%s | %s: %s",
                self.model,
                format_llm_endpoint(self.base_url),
                type(ex).__name__,
                safe_exc_message(ex),
            )
        finally:
            self._sdk_client = None

    async def complete(self, *, system: str, user: str, _skip_success_log: bool = False) -> str:
        client = self._sdk()
        resp = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        choice = resp.choices[0].message.content
        out = choice.strip() if choice else ""
        if not _skip_success_log:
            logger.info(
                "LLM OK | backend=single | model=%r | endpoint=%s | response_chars=%s",
                self.model,
                format_llm_endpoint(self.base_url),
                len(out),
            )
        return out


@dataclass
class LLMChainClient:
    """Ordered providers: on recoverable errors, try the next; empty LLM text is success."""

    _clients: tuple[LLMClient, ...]
    _names: tuple[str | None, ...]

    @classmethod
    def from_resolved(cls, entries: tuple[LLMProviderResolved, ...]) -> LLMChainClient:
        if not entries:
            raise ValueError("LLMChainClient requires at least one resolved provider")
        clients = tuple(
            LLMClient(
                base_url=e.base_url,
                api_key=e.api_key,
                model=e.model,
                timeout=e.timeout_seconds,
                max_retries=e.max_retries,
            )
            for e in entries
        )
        names = tuple(e.name for e in entries)
        return cls(_clients=clients, _names=names)

    @property
    def model(self) -> str:
        return self._clients[0].model

    async def complete(
        self,
        *,
        system: str,
        user: str,
        on_chain_skip: ChainSkipCallback | None = None,
    ) -> str:
        last_exc: BaseException | None = None
        n = len(self._clients)
        for i, client in enumerate(self._clients):
            tag = self._names[i] or f"[{i}]"
            endpoint = format_llm_endpoint(client.base_url)
            try:
                out = await client.complete(system=system, user=user, _skip_success_log=True)
                logger.info(
                    "LLM OK | backend=chain | chain_entry=%r | model=%r | endpoint=%s | response_chars=%s",
                    tag,
                    client.model,
                    endpoint,
                    len(out),
                )
                return out
            except BaseException as e:
                last_exc = e
                if not _should_fallback_to_next_provider(e):
                    logger.error(
                        "LLM FAIL | backend=chain | chain_entry=%r | model=%r | endpoint=%s | %s | exc=%s: %s",
                        tag,
                        client.model,
                        endpoint,
                        _why_not_switching_llm(e),
                        type(e).__name__,
                        safe_exc_message(e),
                    )
                    raise
                if isinstance(e, APIConnectionError):
                    await client.shutdown_sdk_client()
                    logger.info(
                        "LLM HTTP client closed after connection/timeout error | chain_entry=%r | "
                        "model=%r | endpoint=%s (drops TCP so local backends e.g. Ollama can stop work)",
                        tag,
                        client.model,
                        endpoint,
                    )
                if i + 1 >= n:
                    logger.error(
                        "LLM FAIL | backend=chain | chain_entry=%r | model=%r | endpoint=%s | %s | exc=%s: %s",
                        tag,
                        client.model,
                        endpoint,
                        _why_last_chain_entry_failed(e),
                        type(e).__name__,
                        safe_exc_message(e),
                    )
                    raise LLMChainExhaustedError(provider_count=n, last_error=e) from e
                next_client = self._clients[i + 1]
                next_tag = self._names[i + 1] or f"[{i + 1}]"
                next_ep = format_llm_endpoint(next_client.base_url)
                if on_chain_skip is not None:
                    try:
                        await on_chain_skip(
                            ChainSkipNotice(
                                from_entry=tag,
                                from_model=client.model,
                                to_entry=next_tag,
                                to_model=next_client.model,
                                reason=chain_skip_user_reason(e),
                            )
                        )
                    except Exception as cb_ex:
                        logger.warning("on_chain_skip callback failed: %s", cb_ex)
                logger.warning(
                    "LLM chain skip | from_entry=%r | model=%r | endpoint=%s | %s | "
                    "next_entry=%r | next_model=%r | next_endpoint=%s | exc=%s: %s",
                    tag,
                    client.model,
                    endpoint,
                    _why_switching_to_next_llm(e),
                    next_tag,
                    next_client.model,
                    next_ep,
                    type(e).__name__,
                    safe_exc_message(e),
                )
        assert last_exc is not None
        raise last_exc
