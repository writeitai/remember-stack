"""The release-pinned D162 ceiling for the hosted E1 passage binding."""

from collections.abc import Callable
from collections.abc import Sequence
from functools import cache
import gzip
import hashlib
from pathlib import Path
from typing import Final

from tokenizers import Tokenizer

from rememberstack.core.embedding_input_policy import EmbeddingInputRender

HOSTED_EMBEDDING_MODEL: Final = "qwen/qwen3-embedding-8b"
HOSTED_EMBEDDING_PROVIDER: Final = "nebius/bf16"
HOSTED_EMBEDDING_BINDING: Final = (
    f"openrouter/{HOSTED_EMBEDDING_MODEL} only={HOSTED_EMBEDDING_PROVIDER} "
    "allow_fallbacks=false zdr=true data_collection=deny"
)
HOSTED_EMBEDDING_TOKEN_CEILING: Final = 16_384
# The endpoint also rejects a whole request above a total token size with a
# non-retryable HTTP 429. Live probe on 2026-11-28 through this binding: 64
# inputs totalling 49,137 tokens and 4 inputs totalling 45,919 tokens were
# accepted; 6 inputs of ~11.5k tokens (~69k total) were rejected on every try.
# Keep each request at or below this budget. It is at least the per-input
# ceiling, so every fitting input can always be sent on its own.
HOSTED_EMBEDDING_REQUEST_TOKEN_BUDGET: Final = 32_768

# Qwen/Qwen3-Embedding-8B at 1d8ad4ca9b3dd8059ad90a75d4983776a23d44af.
# The upstream post-processor appends the model's <|endoftext|> token.
_TOKENIZER_SHA256: Final = (
    "83cdf8c3a34f68862319cb1810ee7b1e2c0a44e0864ae930194ddb76bb7feb8d"
)


@cache
def _tokenizer() -> Tokenizer:
    path = Path(__file__).with_name("data") / "qwen3_embedding_8b_tokenizer.json.gz"
    payload = gzip.decompress(path.read_bytes())
    if hashlib.sha256(payload).hexdigest() != _TOKENIZER_SHA256:
        raise RuntimeError("bundled Qwen3 embedding tokenizer has the wrong digest")
    return Tokenizer.from_str(payload.decode("utf-8"))


def hosted_embedding_tokens(text: str) -> int:
    """Count the exact D80 request string with its model special tokens."""
    return len(_tokenizer().encode(text, add_special_tokens=True).ids)


def hosted_request_batches[T](
    items: Sequence[T], *, max_items: int, text: Callable[[T], str]
) -> list[tuple[T, ...]]:
    """Split ``items`` in order into requests within the item and token budgets.

    Each request holds at most ``max_items`` inputs and at most
    ``HOSTED_EMBEDDING_REQUEST_TOKEN_BUDGET`` tokens in total. A single input
    always forms a request of its own, so every input that fits the per-input
    ceiling is sent.
    """
    batches: list[tuple[T, ...]] = []
    current: list[T] = []
    used = 0
    for item in items:
        tokens = hosted_embedding_tokens(text(item))
        if current and (
            len(current) >= max_items
            or used + tokens > HOSTED_EMBEDDING_REQUEST_TOKEN_BUDGET
        ):
            batches.append(tuple(current))
            current, used = [], 0
        current.append(item)
        used += tokens
    if current:
        batches.append(tuple(current))
    return batches


def hosted_embedding_fits(rendered: EmbeddingInputRender) -> bool:
    """Empty bodies skip dispatch. Active passages must satisfy the full count."""
    return (
        rendered.skip_reason is not None
        or hosted_embedding_tokens(rendered.embedding_text)
        <= HOSTED_EMBEDDING_TOKEN_CEILING
    )
