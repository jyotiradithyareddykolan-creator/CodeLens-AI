"""Gemini embedding generation (Phase 3, Milestone 4).

Verified against Google's Gemini API documentation (Nov 2026):

* ``gemini-embedding-2`` is the current model (8,192 input tokens per text). It does
  not accept ``taskType``; the task is expressed in the text itself:
  documents ``"title: {title} | text: {content}"``, code queries
  ``"task: code retrieval | query: {content}"``. Truncated dimensions are normalized
  by the service.
* ``gemini-embedding-001`` (available until 2028-05-14, 2,048 input tokens) uses
  ``taskType`` (``RETRIEVAL_DOCUMENT`` / ``CODE_RETRIEVAL_QUERY``) and requires
  client-side normalization for non-3072 dimensions.
* ``POST {base}/models/{model}:batchEmbedContents`` with header ``x-goog-api-key``;
  responses carry ``embeddings[].values`` in request order and
  ``usageMetadata.promptTokenCount``.
* The API reference nests ``taskType``/``title``/``outputDimensionality`` under
  ``embedContentConfig`` while some guide examples put them at the top level. The
  client sends the reference layout and switches to the top-level layout once if the
  API rejects the field.

Vectors are always validated (count, dimension, finite values, non-zero norm) and
L2-normalized. The API key is sent only in a header and is scrubbed from every error
message.
"""

import asyncio
import hashlib
import logging
import math
import random
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional, Sequence

import httpx

from app.core.config import settings
from app.indexing.tokens import estimate_tokens

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({500, 502, 503, 504})
_RETRY_DELAY = re.compile(r"^(\d+(?:\.\d+)?)s$")
LAYOUT_NESTED = "nested"  # embedContentConfig: {...}
LAYOUT_FLAT = "flat"  # taskType/title/outputDimensionality at request top level


class EmbeddingError(Exception):
    """Embedding failed. Messages never contain the API key."""


class EmbeddingConfigurationError(EmbeddingError):
    """Missing/invalid configuration (e.g. no API key). Not retryable."""


class EmbeddingRequestError(EmbeddingError):
    """The API rejected the request (4xx other than 429). Not retryable."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class EmbeddingRateLimitError(EmbeddingError):
    """Rate limited longer than the permitted wait; retry later."""

    def __init__(self, message: str, retry_after_seconds: Optional[float] = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class EmbeddingUnavailableError(EmbeddingError):
    """Transient failures persisted through all retries (5xx, timeouts, network)."""


class EmbeddingResponseError(EmbeddingError):
    """The API returned an invalid embedding payload."""


def uses_text_task_prefix(model: str) -> bool:
    """gemini-embedding-2 family: task expressed in the text, no taskType field."""
    return model.startswith("gemini-embedding-2")


@dataclass(frozen=True)
class EmbeddingInput:
    """One text to embed, already formatted for the model."""

    text: str
    task_type: Optional[str] = None  # only for models that accept taskType
    title: Optional[str] = None  # only with RETRIEVAL_DOCUMENT
    estimated_tokens: int = 0

    def key(self, model: str, dimensions: int) -> str:
        """Stable identity of the resulting vector, used to reuse embeddings."""
        material = "\x1f".join(
            [model, str(dimensions), self.task_type or "", self.title or "", self.text]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def document_input(content: str, *, title: Optional[str], context: Optional[str] = None,
                   model: Optional[str] = None) -> EmbeddingInput:
    """Format a code chunk for embedding as a retrieval document."""
    model = model or settings.GEMINI_EMBEDDING_MODEL
    body = f"{context}\n{content}" if context else content
    if uses_text_task_prefix(model):
        text = f"title: {title or 'none'} | text: {body}"
        return EmbeddingInput(text=text, estimated_tokens=estimate_tokens(text))
    return EmbeddingInput(
        text=body, task_type="RETRIEVAL_DOCUMENT", title=title or None,
        estimated_tokens=estimate_tokens(body) + (estimate_tokens(title) if title else 0),
    )


def query_input(query: str, *, model: Optional[str] = None) -> EmbeddingInput:
    """Format a natural-language question for code retrieval."""
    model = model or settings.GEMINI_EMBEDDING_MODEL
    if uses_text_task_prefix(model):
        text = f"task: code retrieval | query: {query}"
        return EmbeddingInput(text=text, estimated_tokens=estimate_tokens(text))
    return EmbeddingInput(text=query, task_type="CODE_RETRIEVAL_QUERY",
                          estimated_tokens=estimate_tokens(query))


@dataclass
class TokenCalibration:
    """Compares the local estimate with tokens reported by the API."""

    estimated: int = 0
    actual: int = 0
    batches: int = 0

    def record(self, estimated: int, actual: Optional[int]) -> None:
        if actual is None or actual <= 0:
            return
        self.estimated += estimated
        self.actual += actual
        self.batches += 1

    @property
    def ratio(self) -> Optional[float]:
        """actual / estimated. Below 1.0 means the estimator over-counts (safe)."""
        return round(self.actual / self.estimated, 4) if self.estimated else None


@dataclass
class EmbeddingBatchResult:
    vectors: List[List[float]]
    prompt_tokens: Optional[int] = None


@dataclass
class EmbeddingStats:
    requests: int = 0
    texts_embedded: int = 0
    duplicates_skipped: int = 0
    calibration: TokenCalibration = field(default_factory=TokenCalibration)


def _normalize(values: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(v * v for v in values))
    if not math.isfinite(norm) or norm == 0.0:
        raise EmbeddingResponseError("Embedding has zero or non-finite norm")
    return [v / norm for v in values]


def validate_vector(values: object, dimensions: int) -> List[float]:
    """Return a normalized copy of ``values`` or raise EmbeddingResponseError."""
    if not isinstance(values, list) or len(values) != dimensions:
        size = len(values) if isinstance(values, list) else type(values).__name__
        raise EmbeddingResponseError(f"Embedding has {size} values, expected {dimensions}")
    out: List[float] = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise EmbeddingResponseError("Embedding contains a non-numeric or non-finite value")
        out.append(float(v))
    return _normalize(out)


class GeminiEmbedder:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        dimensions: Optional[int] = None,
        *,
        batch_size: Optional[int] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ):
        self.api_key = api_key if api_key is not None else settings.GEMINI_API_KEY
        self.model = (model or settings.GEMINI_EMBEDDING_MODEL).removeprefix("models/")
        self.dimensions = dimensions or settings.GEMINI_EMBEDDING_DIMENSIONS
        self.batch_size = batch_size or settings.GEMINI_EMBEDDING_BATCH_SIZE
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self.layout = LAYOUT_NESTED
        self.stats = EmbeddingStats()

    # ------------------------------------------------------------------ helpers

    def _scrub(self, message: str) -> str:
        if self.api_key:
            message = message.replace(self.api_key, "[REDACTED]")
        return message[:300]

    def _error_message(self, response: httpx.Response) -> str:
        detail = ""
        try:
            body = response.json()
            error = body.get("error", {}) if isinstance(body, dict) else {}
            detail = str(error.get("message") or error.get("status") or "")
        except ValueError:
            pass
        return self._scrub(f"HTTP {response.status_code}" + (f": {detail}" if detail else ""))

    def _retry_delay(self, response: httpx.Response) -> Optional[float]:
        header = response.headers.get("retry-after")
        if header:
            try:
                return max(0.0, float(header))
            except ValueError:
                pass
        try:
            details = response.json().get("error", {}).get("details", [])
        except (ValueError, AttributeError):
            return None
        for detail in details if isinstance(details, list) else []:
            if isinstance(detail, dict) and "retryDelay" in detail:
                match = _RETRY_DELAY.match(str(detail["retryDelay"]))
                if match:
                    return float(match.group(1))
        return None

    def _backoff(self, attempt: int) -> float:
        return min(60.0, 2.0 ** attempt) * (0.5 + random.random() / 2)

    def _request_body(self, inputs: Sequence[EmbeddingInput]) -> dict:
        requests = []
        for item in inputs:
            options: Dict[str, object] = {"outputDimensionality": self.dimensions}
            if item.task_type and not uses_text_task_prefix(self.model):
                options["taskType"] = item.task_type
                if item.title and item.task_type == "RETRIEVAL_DOCUMENT":
                    options["title"] = item.title
            request: Dict[str, object] = {
                "model": f"models/{self.model}",
                "content": {"parts": [{"text": item.text}]},
            }
            if self.layout == LAYOUT_NESTED:
                request["embedContentConfig"] = options
            else:
                request.update(options)
            requests.append(request)
        return {"requests": requests}

    @staticmethod
    def _layout_rejected(response: httpx.Response) -> bool:
        if response.status_code != 400:
            return False
        text = response.text
        return "Unknown name" in text or "embedContentConfig" in text or "Invalid JSON payload" in text

    # ------------------------------------------------------------------ API calls

    async def _embed_batch(self, client: httpx.AsyncClient, inputs: Sequence[EmbeddingInput]) -> EmbeddingBatchResult:
        url = f"{settings.GEMINI_API_BASE_URL}/models/{self.model}:batchEmbedContents"
        headers = {"x-goog-api-key": self.api_key or "", "Content-Type": "application/json"}
        max_retries = max(0, settings.GEMINI_MAX_RETRIES)
        layout_switched = False
        attempt = 0
        while True:
            try:
                self.stats.requests += 1
                response = await client.post(url, headers=headers, json=self._request_body(inputs))
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= max_retries:
                    raise EmbeddingUnavailableError(
                        f"Embedding request failed after {attempt + 1} attempts ({type(exc).__name__})"
                    ) from None
                await self._sleep(self._backoff(attempt))
                attempt += 1
                continue

            if response.status_code == 200:
                return self._parse(response, len(inputs))

            if self._layout_rejected(response) and not layout_switched:
                self.layout = LAYOUT_FLAT if self.layout == LAYOUT_NESTED else LAYOUT_NESTED
                layout_switched = True
                logger.warning("Embedding API rejected request layout; switching to %s layout", self.layout)
                continue

            if response.status_code == 429:
                wait = self._retry_delay(response)
                wait = self._backoff(attempt) if wait is None else wait
                if attempt >= max_retries or wait > settings.GEMINI_MAX_RETRY_WAIT_SECONDS:
                    raise EmbeddingRateLimitError(
                        f"Embedding rate limit exceeded: {self._error_message(response)}",
                        retry_after_seconds=wait,
                    )
                logger.warning("Embedding API rate limited; waiting %.1fs", wait)
                await self._sleep(wait)
                attempt += 1
                continue

            if response.status_code in RETRYABLE_STATUS:
                if attempt >= max_retries:
                    raise EmbeddingUnavailableError(
                        f"Embedding service unavailable: {self._error_message(response)}"
                    )
                await self._sleep(self._backoff(attempt))
                attempt += 1
                continue

            raise EmbeddingRequestError(
                f"Embedding request rejected: {self._error_message(response)}", response.status_code
            )

    def _parse(self, response: httpx.Response, expected: int) -> EmbeddingBatchResult:
        try:
            body = response.json()
        except ValueError:
            raise EmbeddingResponseError("Embedding response is not valid JSON") from None
        embeddings = body.get("embeddings") if isinstance(body, dict) else None
        if not isinstance(embeddings, list) or len(embeddings) != expected:
            got = len(embeddings) if isinstance(embeddings, list) else 0
            raise EmbeddingResponseError(f"Expected {expected} embeddings, received {got}")
        vectors = [
            validate_vector(e.get("values") if isinstance(e, dict) else None, self.dimensions)
            for e in embeddings
        ]
        usage = body.get("usageMetadata") or {}
        tokens = usage.get("promptTokenCount") if isinstance(usage, dict) else None
        return EmbeddingBatchResult(vectors=vectors, prompt_tokens=tokens if isinstance(tokens, int) else None)

    async def embed(self, inputs: Sequence[EmbeddingInput],
                    on_batch: Optional[Callable[[List[EmbeddingInput], List[List[float]]], Awaitable[None]]] = None,
                    ) -> List[List[float]]:
        """Embed ``inputs`` in order. Identical inputs are sent once.

        ``on_batch`` is awaited after each successful batch (used to persist progress so
        an interrupted job does not pay for the same embeddings again).
        """
        if not inputs:
            return []
        if not self.api_key:
            raise EmbeddingConfigurationError("GEMINI_API_KEY is not configured")

        unique: Dict[str, EmbeddingInput] = {}
        for item in inputs:
            unique.setdefault(item.key(self.model, self.dimensions), item)
        self.stats.duplicates_skipped += len(inputs) - len(unique)

        vectors: Dict[str, List[float]] = {}
        keys = list(unique)
        timeout = httpx.Timeout(settings.GEMINI_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            for start in range(0, len(keys), self.batch_size):
                batch_keys = keys[start:start + self.batch_size]
                batch = [unique[k] for k in batch_keys]
                result = await self._embed_batch(client, batch)
                self.stats.texts_embedded += len(batch)
                self.stats.calibration.record(sum(i.estimated_tokens for i in batch), result.prompt_tokens)
                for key, vector in zip(batch_keys, result.vectors):
                    vectors[key] = vector
                if on_batch is not None:
                    await on_batch(batch, result.vectors)
        return [vectors[item.key(self.model, self.dimensions)] for item in inputs]

    async def embed_query(self, query: str) -> List[float]:
        return (await self.embed([query_input(query, model=self.model)]))[0]


def chunk_title(file_path: str, qualified_name: Optional[str]) -> str:
    return f"{file_path} :: {qualified_name}" if qualified_name else file_path


def chunk_input(chunk, *, model: Optional[str] = None) -> EmbeddingInput:
    """Embedding input for a ``Chunk``: file path and symbol as title, context + content."""
    return document_input(
        chunk.content,
        title=chunk_title(chunk.file_path, chunk.qualified_name),
        context=chunk.context,
        model=model,
    )
