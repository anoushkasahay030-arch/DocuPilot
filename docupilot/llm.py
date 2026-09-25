"""Thin async wrapper around the Gemini API (google-genai)."""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from docupilot.config import Settings, get_settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_RETRYABLE = {429, 500, 502, 503, 504}


class LLM:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        if not self.settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
        self.client = genai.Client(api_key=self.settings.gemini_api_key)
        # Models that rejected a zero thinking budget; we stop sending it to them.
        self._no_thinking_cfg: set[str] = set()

    @property
    def fast(self) -> str:
        return self.settings.gemini_model

    @property
    def strong(self) -> str:
        return self.settings.gemini_strong_model

    def _config(self, model: str, system: str | None, temperature: float, fast: bool, **extra) -> types.GenerateContentConfig:
        cfg = dict(system_instruction=system, temperature=temperature, **extra)
        budget = 0 if fast else self.settings.strong_thinking_budget
        if budget is not None and model not in self._no_thinking_cfg:
            # Routing / SQL / verification don't benefit from long thinking; keep them snappy.
            cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=budget)
        return types.GenerateContentConfig(**cfg)

    async def _call(self, fn, model: str, make_cfg):
        delay = 1.0
        for attempt in range(4):
            try:
                return await fn(model=model, config=make_cfg())
            except errors.APIError as e:
                if e.code == 400 and "thinking" in str(e).lower() and model not in self._no_thinking_cfg:
                    self._no_thinking_cfg.add(model)
                    continue
                if e.code in _RETRYABLE and attempt < 3:
                    log.warning("Gemini %s error, retrying in %.1fs: %s", e.code, delay, e)
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise
        raise RuntimeError(f"Gemini call to {model} failed after retries")

    async def generate(self, prompt: str, *, system: str | None = None, model: str | None = None,
                       temperature: float = 0.2) -> str:
        model = model or self.fast
        fast = model == self.fast

        async def fn(model, config):
            return await self.client.aio.models.generate_content(model=model, contents=prompt, config=config)

        resp = await self._call(fn, model, lambda: self._config(model, system, temperature, fast))
        return resp.text or ""

    async def generate_json(self, prompt: str, schema: type[T], *, system: str | None = None,
                            model: str | None = None, temperature: float = 0.0) -> T:
        model = model or self.fast
        fast = model == self.fast

        async def fn(model, config):
            return await self.client.aio.models.generate_content(model=model, contents=prompt, config=config)

        resp = await self._call(fn, model, lambda: self._config(
            model, system, temperature, fast, response_mime_type="application/json", response_schema=schema))
        if isinstance(resp.parsed, schema):
            return resp.parsed
        return schema.model_validate_json(resp.text or "{}")

    async def stream(self, prompt: str, *, system: str | None = None, model: str | None = None,
                     temperature: float = 0.2) -> AsyncIterator[str]:
        model = model or self.strong
        fast = model == self.fast

        async def fn(model, config):
            return await self.client.aio.models.generate_content_stream(model=model, contents=prompt, config=config)

        stream = await self._call(fn, model, lambda: self._config(model, system, temperature, fast))
        async for chunk in stream:
            if chunk.text:
                yield chunk.text
