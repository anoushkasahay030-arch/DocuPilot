"""Async local Ollama wrapper: schema-validated JSON, streaming, and bounded retries."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from typing import TypeVar, cast

import httpx
from ollama import AsyncClient, ChatResponse, ResponseError
from pydantic import BaseModel, ValidationError

from docupilot.config import Settings, get_settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_RETRYABLE = {429, 500, 502, 503, 504}


class LLM:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.client = AsyncClient(
            host=self.settings.ollama_base_url,
            timeout=httpx.Timeout(self.settings.ollama_timeout_s, connect=5.0),
            trust_env=False,
        )

    @property
    def fast(self) -> str:
        return self.settings.ollama_model

    @property
    def strong(self) -> str:
        return self.settings.ollama_strong_model or self.fast

    @staticmethod
    def _messages(prompt: str, system: str | None) -> list[dict]:
        messages = [{"role": "system", "content": system}] if system else []
        return [*messages, {"role": "user", "content": prompt}]

    def _error(self, error: Exception, model: str, emitted: bool) -> RuntimeError:
        host = self.settings.ollama_base_url
        if isinstance(error, (ConnectionError, httpx.ConnectError)):
            detail = f"Cannot connect to Ollama at {host}. Start Ollama (ollama serve) and check OLLAMA_BASE_URL."
        elif isinstance(error, httpx.TimeoutException):
            detail = (f"Ollama timed out running {model}. Model loading or generation may be slow; "
                      "check the server, use a smaller model, or increase OLLAMA_TIMEOUT_S.")
        elif isinstance(error, ResponseError) and error.status_code == 404:
            detail = (f"Ollama model {model!r} was not found at {host}. "
                      f"Download it on that server with: ollama pull {model}")
        else:
            detail = f"Ollama request for {model!r} at {host} failed: {error}"
        if emitted:
            detail += " The response was interrupted; please retry the question."
        return RuntimeError(detail)

    async def _request(self, messages: list[dict], model: str, temperature: float,
                       *, stream: bool = False, schema: dict | None = None) -> AsyncIterator[str]:
        # The streaming HTTP request starts when the SDK iterator is consumed, so retries must
        # cover iteration too. Once content reaches the caller, replay would duplicate its answer.
        emitted = False
        for attempt in range(4):
            try:
                response = await self.client.chat(
                    model=model, messages=messages, stream=stream,
                    options={"temperature": temperature, "num_ctx": self.settings.ollama_num_ctx},
                    **({"format": schema} if schema is not None else {}),
                )
                if stream:
                    done = False
                    async with aclosing(cast(AsyncGenerator[ChatResponse, None], response)) as chunks:
                        async for chunk in chunks:
                            done = bool(chunk.done)
                            if chunk.message.content:
                                emitted = True
                                yield chunk.message.content
                    if not done:
                        raise httpx.RemoteProtocolError("Ollama stream ended before its completion message.")
                else:
                    yield cast(ChatResponse, response).message.content or ""
                return
            except (ResponseError, ConnectionError, httpx.RequestError) as error:
                retryable = (isinstance(error, ResponseError) and error.status_code in _RETRYABLE
                             or isinstance(error, (httpx.ReadError, httpx.RemoteProtocolError)))
                if retryable and not emitted and attempt < 3:
                    delay = 2.0 ** attempt
                    log.warning("Ollama request for %s failed; retrying in %.1fs: %s", model, delay, error)
                    await asyncio.sleep(delay)
                    continue
                raise self._error(error, model, emitted) from error

    async def generate(self, prompt: str, *, system: str | None = None, model: str | None = None,
                       temperature: float = 0.2) -> str:
        return "".join([text async for text in self._request(
            self._messages(prompt, system), model or self.fast, temperature)])

    async def generate_json(self, prompt: str, schema: type[T], *, system: str | None = None,
                            model: str | None = None, temperature: float = 0.0,
                            images: list[bytes] | None = None) -> T:
        model = model or self.fast
        json_schema = schema.model_json_schema()
        instruction = "Return only a JSON object matching this schema:\n" + json.dumps(json_schema)
        messages = self._messages(prompt, f"{system}\n\n{instruction}" if system else instruction)
        if images:
            messages[-1]["images"] = images
        for attempt in range(2):
            content = "".join([text async for text in self._request(
                messages, model, temperature, schema=json_schema)])
            try:
                return schema.model_validate_json(content)
            except ValidationError as error:
                if attempt:
                    raise RuntimeError(
                        f"Ollama model {model!r} returned invalid {schema.__name__} JSON after two attempts. "
                        "Retry the question or choose a model with better structured-output support."
                    ) from error
                messages.extend([
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": (
                        "Correct the previous response to match the JSON schema. Return only JSON.\n"
                        f"Validation errors: {error.json(include_input=False, include_url=False)}"
                    )},
                ])
        raise AssertionError("unreachable")

    async def stream(self, prompt: str, *, system: str | None = None, model: str | None = None,
                     temperature: float = 0.2) -> AsyncIterator[str]:
        async with aclosing(self._request(
            self._messages(prompt, system), model or self.strong, temperature, stream=True,
        )) as chunks:
            async for text in chunks:
                yield text
