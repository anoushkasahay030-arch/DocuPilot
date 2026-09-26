"""Exercise the real Ollama client against a mock HTTP server, without keys or model downloads."""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from ollama import AsyncClient
from pydantic import ValidationError

import docupilot.llm as llm_module
from docupilot.agents.router import RouteDecision
from docupilot.agents.table import SQLFix, SQLPlan, SQLQuery
from docupilot.agents.verifier import Claim, Verification
from docupilot.config import Settings
from docupilot.llm import LLM


def response(content, *, done=True):
    return {"message": {"role": "assistant", "content": content}, "done": done}


def ndjson(*items):
    return httpx.Response(200, content="\n".join(json.dumps(item) for item in items) + "\n")


@pytest.fixture
def make_llm(monkeypatch):
    # Avoid inheriting developer configuration or credentials in these isolated tests.
    for name in ("OLLAMA_MODEL", "OLLAMA_STRONG_MODEL", "OLLAMA_BASE_URL", "OLLAMA_NUM_CTX", "OLLAMA_TIMEOUT_S",
                 "GEMINI_API_KEY", "GOOGLE_API_KEY", "OLLAMA_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    def build(*outcomes, **settings):
        requests = []
        pending = iter(outcomes)

        def handle(request):
            requests.append(request)
            outcome = next(pending)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        def client(**kwargs):
            return AsyncClient(**kwargs, transport=httpx.MockTransport(handle))

        monkeypatch.setattr(llm_module, "AsyncClient", client)
        return LLM(Settings(_env_file=None, **settings)), requests

    return build


@pytest.fixture
def sleeps(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(llm_module.asyncio, "sleep", sleep)
    return sleep


def body(request):
    return json.loads(request.content)


def test_default_generation_without_keys(make_llm):
    llm, requests = make_llm(httpx.Response(200, json=response("hello")))
    assert llm.fast == llm.strong == "qwen2.5:7b"
    assert asyncio.run(llm.generate("Hi", system="Be concise")) == "hello"
    request = requests[0]
    assert str(request.url) == "http://localhost:11434/api/chat"
    assert "authorization" not in request.headers
    data = body(request)
    assert data["model"] == "qwen2.5:7b"
    assert data["messages"] == [{"role": "system", "content": "Be concise"}, {"role": "user", "content": "Hi"}]
    assert data["options"]["temperature"] == 0.2
    assert data["options"]["num_ctx"] == 16384
    assert data["stream"] is False
    assert request.extensions["timeout"]["read"] == 300


def test_environment_and_model_overrides(make_llm, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2:3b")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "8192")
    monkeypatch.setenv("OLLAMA_TIMEOUT_S", "60")
    llm, requests = make_llm(httpx.Response(200, json=response("ok")))
    assert llm.fast == llm.strong == "llama3.2:3b"
    assert asyncio.run(llm.generate("Hi", model="phi3:mini", temperature=0.7)) == "ok"
    assert str(requests[0].url) == "http://127.0.0.1:11435/api/chat"
    assert body(requests[0])["model"] == "phi3:mini"
    assert body(requests[0])["options"] == {"temperature": 0.7, "num_ctx": 8192}
    assert requests[0].extensions["timeout"]["read"] == 60


@pytest.mark.parametrize("value", [
    RouteDecision(standalone_question="Revenue?", route="table", target_files=["sales.csv"]),
    SQLPlan(queries=[SQLQuery(purpose="Total revenue", sql="SELECT SUM(revenue) FROM sales")]),
    SQLFix(sql="SELECT SUM(revenue) FROM sales"),
    Verification(claims=[Claim(claim="Revenue is 42", citations=[1], verdict="supported")],
                 revised_answer="Revenue is 42 [1]."),
])
def test_structured_agent_outputs(make_llm, value):
    schema = type(value)
    llm, requests = make_llm(httpx.Response(200, json=response(value.model_dump_json())))
    actual = asyncio.run(llm.generate_json("Question", schema, system="Agent instructions", model="custom"))
    assert actual == value
    data = body(requests[0])
    assert data["model"] == "custom"
    assert data["format"] == schema.model_json_schema()
    assert data["options"]["temperature"] == 0
    assert data["messages"][0]["content"].startswith("Agent instructions")
    assert json.dumps(schema.model_json_schema()) in data["messages"][0]["content"]


@pytest.mark.parametrize("invalid", ["not JSON", "", '{"route":"invalid","standalone_question":"Q"}', "{}"])
def test_invalid_json_gets_one_corrective_retry(make_llm, invalid):
    valid = RouteDecision(standalone_question="Question", route="docs")
    llm, requests = make_llm(httpx.Response(200, json=response(invalid)),
                             httpx.Response(200, json=response(valid.model_dump_json())))
    assert asyncio.run(llm.generate_json("Question", RouteDecision)) == valid
    assert len(requests) == 2
    correction = body(requests[1])["messages"]
    assert correction[-2]["role"] == "assistant"
    assert correction[-2].get("content", "") == invalid  # SDK omits empty content on the wire.
    assert "Validation errors" in correction[-1]["content"]
    assert body(requests[1])["format"] == RouteDecision.model_json_schema()


def test_invalid_json_exhaustion_is_explicit(make_llm):
    llm, requests = make_llm(*(httpx.Response(200, json=response("{}")) for _ in range(2)))
    with pytest.raises(RuntimeError, match="invalid RouteDecision JSON after two attempts"):
        asyncio.run(llm.generate_json("Question", RouteDecision))
    assert len(requests) == 2


async def collect(llm, **kwargs):
    return [chunk async for chunk in llm.stream("Question", **kwargs)]


def test_stream_skips_empty_chunks_and_uses_strong_model(make_llm):
    llm, requests = make_llm(ndjson(response("", done=False), response("Hello ", done=False),
                                   response("world", done=False), response("")),
                             ollama_strong_model="mistral:7b")
    assert asyncio.run(collect(llm)) == ["Hello ", "world"]
    assert body(requests[0])["model"] == "mistral:7b"
    assert body(requests[0])["stream"] is True


def test_stream_model_override(make_llm):
    llm, requests = make_llm(ndjson(response("hello")), ollama_strong_model="mistral:7b")
    assert asyncio.run(collect(llm, model="phi3:mini", temperature=0.1)) == ["hello"]
    assert body(requests[0])["model"] == "phi3:mini"
    assert body(requests[0])["options"]["temperature"] == 0.1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_server_error_retries(make_llm, sleeps, status):
    llm, requests = make_llm(httpx.Response(status, json={"error": "busy"}),
                             httpx.Response(200, json=response("ok")))
    assert asyncio.run(llm.generate("Q")) == "ok"
    assert len(requests) == 2
    sleeps.assert_awaited_once_with(1.0)


def test_server_retries_are_bounded(make_llm, sleeps):
    llm, requests = make_llm(*(httpx.Response(503, json={"error": "busy"}) for _ in range(4)))
    with pytest.raises(RuntimeError, match="busy"):
        asyncio.run(llm.generate("Q"))
    assert len(requests) == 4
    assert [call.args[0] for call in sleeps.await_args_list] == [1, 2, 4]


@pytest.mark.parametrize("error, message", [
    (httpx.ConnectError("refused"), "ollama serve"),
    (httpx.ReadTimeout("slow"), "OLLAMA_TIMEOUT_S"),
    (httpx.Response(404, json={"error": "model not found"}), "ollama pull qwen2.5:7b"),
    (httpx.Response(400, json={"error": "bad request"}), "bad request"),
])
@pytest.mark.parametrize("stream", [False, True])
def test_actionable_errors_do_not_retry(make_llm, sleeps, error, message, stream):
    llm, requests = make_llm(error)
    with pytest.raises(RuntimeError, match=message):
        asyncio.run(collect(llm) if stream else llm.generate("Q"))
    assert len(requests) == 1
    sleeps.assert_not_awaited()


def test_stream_retries_before_first_text(make_llm, sleeps):
    llm, requests = make_llm(httpx.Response(503, json={"error": "busy"}), ndjson(response("complete")))
    assert asyncio.run(collect(llm)) == ["complete"]
    assert len(requests) == 2
    sleeps.assert_awaited_once_with(1.0)


def test_read_error_retries_before_first_text(make_llm, sleeps):
    llm, requests = make_llm(httpx.ReadError("disconnected"), ndjson(response("complete")))
    assert asyncio.run(collect(llm)) == ["complete"]
    assert len(requests) == 2


def test_stream_without_completion_retries_before_first_text(make_llm, sleeps):
    llm, requests = make_llm(ndjson(response("", done=False)), ndjson(response("complete")))
    assert asyncio.run(collect(llm)) == ["complete"]
    assert len(requests) == 2


@pytest.mark.parametrize("with_error", [True, False])
def test_interrupted_stream_never_replays_partial_answer(make_llm, sleeps, with_error):
    items = [response("Partial answer", done=False)]
    if with_error:
        items.append({"error": "runner disconnected"})
    llm, requests = make_llm(ndjson(*items))
    seen = []

    async def consume():
        async for text in llm.stream("Question"):
            seen.append(text)

    with pytest.raises(RuntimeError, match="response was interrupted"):
        asyncio.run(consume())
    assert seen == ["Partial answer"]
    assert len(requests) == 1
    sleeps.assert_not_awaited()


def test_cancelled_stream_closes_response(make_llm):
    closed = []

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield (json.dumps(response("first", done=False)) + "\n").encode()
            raise AssertionError("Cancelled stream should not request more data")

        async def aclose(self):
            closed.append(True)

    llm, _ = make_llm(httpx.Response(200, stream=SlowStream()))

    async def consume_one():
        stream = llm.stream("Question")
        assert await anext(stream) == "first"
        await stream.aclose()

    asyncio.run(consume_one())
    assert closed == [True]


@pytest.mark.parametrize("setting", ["ollama_num_ctx", "ollama_timeout_s"])
def test_positive_resource_limits(setting):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{setting: 0})


def test_existing_env_can_keep_unrelated_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("TOP_K", raising=False)
    path = tmp_path / ".env"
    path.write_text("GEMINI_API_KEY=unused\nGEMINI_MODEL=retired\nSTRONG_THINKING_BUDGET=0\nTOP_K=6\n")
    settings = Settings(_env_file=path)
    assert settings.top_k == 6
    assert not hasattr(settings, "gemini_api_key")
