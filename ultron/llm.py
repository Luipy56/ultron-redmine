from __future__ import annotations

from dataclasses import dataclass, field

from openai import AsyncOpenAI


@dataclass
class LLMClient:
    base_url: str
    api_key: str
    model: str
    timeout: float = 120.0
    _sdk_client: AsyncOpenAI | None = field(default=None, repr=False)

    def _sdk(self) -> AsyncOpenAI:
        if self._sdk_client is None:
            self._sdk_client = AsyncOpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=self.timeout,
            )
        return self._sdk_client

    async def complete(self, *, system: str, user: str) -> str:
        client = self._sdk()
        resp = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        choice = resp.choices[0].message.content
        if not choice:
            return ""
        return choice.strip()
