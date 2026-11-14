"""Measure the token estimator against real Gemini token usage (live, opt-in).

    python -m app.indexing.calibrate --sample 40

Chunks this project's own source files, embeds a sample with the configured model
and prints actual/estimated tokens from the API's ``usageMetadata``. Requires
GEMINI_API_KEY; sends the sampled source code to the Gemini API. Writes nothing.
"""

import argparse
import asyncio
import random
from pathlib import Path

from app.core.config import settings
from app.indexing.chunking import chunk_source
from app.indexing.embeddings import GeminiEmbedder, chunk_input
from app.indexing.file_filter import LANGUAGES_BY_EXTENSION

ROOT = Path(__file__).resolve().parents[3]


async def main(sample: int) -> None:
    files = [p for base in ("backend/app", "frontend/src") for p in (ROOT / base).rglob("*")
             if p.suffix in LANGUAGES_BY_EXTENSION and "__pycache__" not in p.parts]
    chunks = [c for p in files for c in chunk_source(p.relative_to(ROOT).as_posix(),
              LANGUAGES_BY_EXTENSION[p.suffix], p.read_text(encoding="utf-8"))]
    picked = random.Random(7).sample(chunks, min(sample, len(chunks)))
    embedder = GeminiEmbedder(batch_size=10)
    await embedder.embed([chunk_input(c) for c in picked])
    cal = embedder.stats.calibration
    print(f"model={embedder.model} dims={embedder.dimensions} chunks={len(picked)} batches={cal.batches}")
    print(f"estimated={cal.estimated} actual={cal.actual} ratio(actual/estimated)={cal.ratio}")
    if cal.ratio:
        current = settings.CHUNK_TOKEN_ESTIMATE_MULTIPLIER
        print(f"current multiplier={current}; estimator over-counts by "
              f"{(1 / cal.ratio - 1) * 100:.0f}%" if cal.ratio < 1 else
              f"estimator UNDER-counts: raise CHUNK_TOKEN_ESTIMATE_MULTIPLIER to >= {current / cal.ratio:.2f}")
    else:
        print("The API returned no usageMetadata; calibration unavailable for this model.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=40)
    asyncio.run(main(parser.parse_args().sample))
