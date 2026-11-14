"""Gemini embedding client tests. All HTTP is mocked with httpx.MockTransport."""

import json
import logging
import math

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.indexing.chunking import chunk_source
from app.indexing.embeddings import (
    LAYOUT_FLAT,
    EmbeddingConfigurationError,
    EmbeddingInput,
    EmbeddingRateLimitError,
    EmbeddingRequestError,
    EmbeddingResponseError,
    EmbeddingUnavailableError,
    GeminiEmbedder,
    chunk_input,
    document_input,
    query_input,
    validate_vector,
)

API_KEY = "test-gemini-key-SHOULD-NOT-LEAK"
DIMS = 8


def vec(seed: int, dims: int = DIMS):
    return [float(seed + i + 1) for i in range(dims)]


class FakeGemini:
    """Responds with deterministic vectors; scripted failures are consumed first."""

    def __init__(self, failures=(), dims=DIMS, usage=True):
        self.failures = list(failures)
        self.dims = dims
        self.usage = usage
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        body = json.loads(request.content)
        embeddings = [{"values": vec(len(r["content"]["parts"][0]["text"]), self.dims)} for r in body["requests"]]
        payload = {"embeddings": embeddings}
        if self.usage:
            payload["usageMetadata"] = {"promptTokenCount": 7 * len(embeddings)}
        return httpx.Response(200, json=payload)


def make(fake, model="gemini-embedding-2", batch_size=2, sleeps=None):
    async def fake_sleep(seconds):
        if sleeps is not None:
            sleeps.append(seconds)

    return GeminiEmbedder(api_key=API_KEY, model=model, dimensions=DIMS, batch_size=batch_size,
                          transport=httpx.MockTransport(fake), sleep=fake_sleep)


def inputs(n, model="gemini-embedding-2"):
    return [document_input(f"def f{i}(): return {i}", title=f"a.py :: f{i}", model=model) for i in range(n)]


@pytest.mark.asyncio
async def test_batches_order_normalization_and_request_format():
    fake = FakeGemini()
    embedder = make(fake, batch_size=2)
    items = inputs(5)
    vectors = await embedder.embed(items)

    assert len(fake.requests) == 3  # 2 + 2 + 1
    assert len(vectors) == 5
    for item, vector in zip(items, vectors):
        expected = vec(len(item.text))
        norm = math.sqrt(sum(v * v for v in expected))
        assert vector == pytest.approx([v / norm for v in expected])
        assert sum(v * v for v in vector) == pytest.approx(1.0)

    request = fake.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/v1beta/models/gemini-embedding-2:batchEmbedContents"
    assert request.headers["x-goog-api-key"] == API_KEY
    assert API_KEY not in str(request.url)  # never in the URL
    body = json.loads(request.content)
    first = body["requests"][0]
    assert first["model"] == "models/gemini-embedding-2"
    assert first["content"]["parts"][0]["text"].startswith("title: a.py :: f0 | text: def f0()")
    # gemini-embedding-2 does not accept taskType.
    assert first["embedContentConfig"] == {"outputDimensionality": DIMS}


@pytest.mark.asyncio
async def test_embedding_001_uses_task_type_and_title():
    fake = FakeGemini()
    embedder = make(fake, model="gemini-embedding-001")
    await embedder.embed(inputs(1, model="gemini-embedding-001"))
    config = json.loads(fake.requests[0].content)["requests"][0]["embedContentConfig"]
    assert config == {"outputDimensionality": DIMS, "taskType": "RETRIEVAL_DOCUMENT", "title": "a.py :: f0"}


def test_query_and_document_formatting_per_model():
    assert query_input("where is auth?", model="gemini-embedding-2").text == "task: code retrieval | query: where is auth?"
    q = query_input("where is auth?", model="gemini-embedding-001")
    assert (q.text, q.task_type) == ("where is auth?", "CODE_RETRIEVAL_QUERY")
    d = document_input("x = 1", title=None, context="class A", model="gemini-embedding-2")
    assert d.text == "title: none | text: class A\nx = 1"


def test_chunk_input_uses_path_symbol_and_context():
    chunks = chunk_source("svc/app.py", "python", "def handler(event):\n    return event\n")
    item = chunk_input(chunks[0], model="gemini-embedding-2")
    assert item.text == "title: svc/app.py :: handler | text: def handler(event):\n    return event\n"
    assert item.estimated_tokens > 0


@pytest.mark.asyncio
async def test_identical_inputs_are_embedded_once():
    fake = FakeGemini()
    embedder = make(fake, batch_size=10)
    item = inputs(1)[0]
    vectors = await embedder.embed([item, item, item])
    assert len(fake.requests) == 1
    assert len(json.loads(fake.requests[0].content)["requests"]) == 1
    assert vectors[0] == vectors[1] == vectors[2]
    assert embedder.stats.duplicates_skipped == 2


@pytest.mark.asyncio
async def test_retries_server_errors_and_timeouts_with_backoff():
    sleeps = []
    fake = FakeGemini(failures=[httpx.Response(503), httpx.ReadTimeout("slow"), httpx.Response(500)])
    vectors = await make(fake, sleeps=sleeps).embed(inputs(1))
    assert len(vectors) == 1
    assert len(fake.requests) == 4 and len(sleeps) == 3


@pytest.mark.asyncio
async def test_persistent_server_errors_raise_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_MAX_RETRIES", 2)
    fake = FakeGemini(failures=[httpx.Response(503)] * 3)
    with pytest.raises(EmbeddingUnavailableError):
        await make(fake, sleeps=[]).embed(inputs(1))
    assert len(fake.requests) == 3


@pytest.mark.asyncio
async def test_rate_limit_honours_retry_info_and_retry_after():
    sleeps = []
    retry_info = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12s"}]}}
    fake = FakeGemini(failures=[httpx.Response(429, json=retry_info), httpx.Response(429, headers={"retry-after": "3"})])
    await make(fake, sleeps=sleeps).embed(inputs(1))
    assert sleeps == [12.0, 3.0]


@pytest.mark.asyncio
async def test_long_rate_limit_raises_with_retry_after(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_MAX_RETRY_WAIT_SECONDS", 30)
    quota = {"error": {"message": "Quota exceeded", "details": [{"retryDelay": "3600s"}]}}
    sleeps = []
    with pytest.raises(EmbeddingRateLimitError) as exc:
        await make(FakeGemini(failures=[httpx.Response(429, json=quota)]), sleeps=sleeps).embed(inputs(1))
    assert exc.value.retry_after_seconds == 3600.0 and sleeps == []


@pytest.mark.asyncio
async def test_client_errors_are_not_retried_and_never_leak_the_key():
    body = {"error": {"code": 400, "message": f"API key not valid: {API_KEY}", "status": "INVALID_ARGUMENT"}}
    fake = FakeGemini(failures=[httpx.Response(400, json=body)])
    with pytest.raises(EmbeddingRequestError) as exc:
        await make(fake, sleeps=[]).embed(inputs(1))
    assert len(fake.requests) == 1
    assert API_KEY not in str(exc.value) and "[REDACTED]" in str(exc.value)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_request_layout_falls_back_once_when_rejected():
    rejection = httpx.Response(400, json={"error": {"message": 'Invalid JSON payload received. Unknown name "embedContentConfig"'}})
    fake = FakeGemini(failures=[rejection])
    embedder = make(fake)
    await embedder.embed(inputs(1))
    assert embedder.layout == LAYOUT_FLAT
    second = json.loads(fake.requests[1].content)["requests"][0]
    assert "embedContentConfig" not in second and second["outputDimensionality"] == DIMS


@pytest.mark.parametrize(
    "payload",
    [
        {"embeddings": [{"values": [1.0] * (DIMS - 1)}]},  # wrong dimension
        {"embeddings": [{"values": [0.0] * DIMS}]},  # zero vector
        {"embeddings": [{"values": [1.0] * (DIMS - 1) + ["x"]}]},  # non-numeric
        {"embeddings": []},  # count mismatch
        {"unexpected": True},
    ],
)
@pytest.mark.asyncio
async def test_invalid_responses_are_rejected(payload):
    fake = FakeGemini(failures=[httpx.Response(200, json=payload)])
    with pytest.raises(EmbeddingResponseError):
        await make(fake, sleeps=[]).embed(inputs(1))


@pytest.mark.asyncio
async def test_non_json_success_response_is_rejected():
    fake = FakeGemini(failures=[httpx.Response(200, content=b"<html>proxy error</html>")])
    with pytest.raises(EmbeddingResponseError):
        await make(fake, sleeps=[]).embed(inputs(1))


def test_validate_vector_rejects_non_finite_values():
    with pytest.raises(EmbeddingResponseError):
        validate_vector([1.0, float("nan")], 2)
    with pytest.raises(EmbeddingResponseError):
        validate_vector([1.0, float("inf")], 2)
    with pytest.raises(EmbeddingResponseError):
        validate_vector([True, 1.0], 2)
    assert validate_vector([3, 4], 2) == pytest.approx([0.6, 0.8])


@pytest.mark.asyncio
async def test_missing_api_key_fails_before_any_request():
    fake = FakeGemini()
    embedder = GeminiEmbedder(api_key="", model="gemini-embedding-2", dimensions=DIMS,
                              transport=httpx.MockTransport(fake))
    with pytest.raises(EmbeddingConfigurationError):
        await embedder.embed(inputs(1))
    assert fake.requests == []
    assert await embedder.embed([]) == []


@pytest.mark.asyncio
async def test_calibration_records_api_token_usage():
    fake = FakeGemini()
    embedder = make(fake, batch_size=2)
    items = inputs(4)
    await embedder.embed(items)
    cal = embedder.stats.calibration
    assert cal.batches == 2
    assert cal.actual == 28  # 7 tokens x 4 texts from usageMetadata
    assert cal.estimated == sum(i.estimated_tokens for i in items)
    assert cal.ratio == round(28 / cal.estimated, 4)


@pytest.mark.asyncio
async def test_calibration_skipped_without_usage_metadata():
    embedder = make(FakeGemini(usage=False))
    await embedder.embed(inputs(2))
    assert embedder.stats.calibration.ratio is None


@pytest.mark.asyncio
async def test_on_batch_callback_receives_each_batch():
    seen = []

    async def on_batch(batch, vectors):
        seen.append((len(batch), len(vectors)))

    await make(FakeGemini(), batch_size=2).embed(inputs(3), on_batch=on_batch)
    assert seen == [(2, 2), (1, 1)]


def test_embedding_keys_depend_on_model_dimensions_and_text():
    item = EmbeddingInput(text="x")
    assert item.key("m", 768) == EmbeddingInput(text="x").key("m", 768)
    assert len({item.key("m", 768), item.key("m", 1536), item.key("n", 768),
                EmbeddingInput(text="y").key("m", 768)}) == 4


def test_embedding_settings_validation_and_model_prefix():
    assert Settings(_env_file=None, GEMINI_EMBEDDING_MODEL="models/gemini-embedding-001").GEMINI_EMBEDDING_MODEL == "gemini-embedding-001"
    for bad in ({"GEMINI_EMBEDDING_DIMENSIONS": 64}, {"GEMINI_EMBEDDING_DIMENSIONS": 4096},
                {"GEMINI_EMBEDDING_BATCH_SIZE": 0}):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **bad)


@pytest.mark.asyncio
async def test_api_key_never_appears_in_logs(caplog):
    fake = FakeGemini(failures=[httpx.Response(429, headers={"retry-after": "1"}),
                                httpx.Response(400, json={"error": {"message": f"bad {API_KEY}"}})])
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(EmbeddingRequestError):
            await make(fake, sleeps=[]).embed(inputs(1))
    assert API_KEY not in caplog.text
