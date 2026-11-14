"""Conservative token-count estimation for source code.

Why an estimator instead of the real tokenizer
----------------------------------------------
Gemini embedding models enforce a per-text input limit (2,048 tokens for
gemini-embedding-001; excess is silently truncated by default). The exact count is
available from the Gemini API (``countTokens`` / usage metadata), but calling it for
every chunk would double API traffic. The only offline option is the experimental
``google-genai`` ``LocalTokenizer``, which downloads tokenizer data at runtime and is
not documented to support embedding models. Neither is acceptable as a hard
dependency of chunking.

So chunking uses this dependency-free estimator, designed to *over*-estimate for
code so chunks stay under the limit:

* identifiers are split on case/underscore boundaries; each piece costs
  ``ceil(len / 4)`` tokens (minimum 1)
* digits cost one token each (SentencePiece-style tokenizers split numbers per digit)
* every punctuation/operator character costs one token
* whitespace runs containing a newline, or longer than one space, cost one token
* every non-ASCII character costs one token

``CHUNK_TOKEN_ESTIMATE_MULTIPLIER`` scales the result. Milestone 4 will measure the
estimate against real ``countTokens`` results and tune that multiplier.
"""

import math
import re

from app.core.config import settings

_PIECE = re.compile(
    r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|"  # identifier pieces (camelCase aware)
    r"[0-9]|"  # digits, one each
    r"[ \t]*\r?\n[\s]*|[ \t]{2,}|"  # line breaks / indentation runs
    r"[\x21-\x7e]|"  # single ASCII punctuation/operator characters
    r"[^\x00-\x7f]"  # any non-ASCII character
)


def estimate_tokens(text: str) -> int:
    """Estimated token count of ``text`` (an upper-leaning approximation)."""
    if not text:
        return 0
    count = 0
    for match in _PIECE.finditer(text):
        piece = match.group()
        first = piece[0]
        if first.isascii() and first.isalpha():
            count += max(1, math.ceil(len(piece) / 4))
        else:
            count += 1
    return max(1, math.ceil(count * settings.CHUNK_TOKEN_ESTIMATE_MULTIPLIER))
