"""
core/research_budget.py - Token budget helpers for research.

Works with the SAME num_ctx the chat already uses (default 8192).
The model is never reloaded.

Budget fractions (of num_ctx):
  Input cap  : 55% for evidence/ledger calls, 45% for report sections
  num_predict: 500 for evidence notes, 700 for skeptic pass, 1200 per report section
"""

from __future__ import annotations

from core.context_manager import estimate_tokens

# Fraction of num_ctx available for the input prompt
_INPUT_FRAC_EVIDENCE = 0.55
_INPUT_FRAC_REPORT = 0.45

# Fixed output token caps
NUM_PREDICT_EVIDENCE = 500
NUM_PREDICT_SKEPTIC = 700
NUM_PREDICT_SECTION = 1200
NUM_PREDICT_REPAIR = 300
NUM_PREDICT_LEDGER = 500


def input_budget(num_ctx: int, fraction: float = _INPUT_FRAC_EVIDENCE) -> int:
    """Max input tokens for one model call (55% of num_ctx by default)."""
    return max(512, int(num_ctx * fraction))


def trim_chunks_to_budget(
    chunks: list[dict],
    budget_tokens: int,
    overhead_text: str = "",
) -> list[dict]:
    """
    Drop lowest-ranked chunks (highest index = lowest BM25 score) until
    the combined text fits within budget_tokens.
    Preserves chunk order (highest score first).
    """
    overhead = estimate_tokens(overhead_text)
    remaining = budget_tokens - overhead
    kept: list[dict] = []
    for chunk in chunks:
        chunk_tokens = estimate_tokens(chunk.get("text", ""))
        if remaining - chunk_tokens < 50:
            break
        kept.append(chunk)
        remaining -= chunk_tokens
    return kept


def trim_text_to_budget(text: str, budget_tokens: int) -> str:
    """Truncate a plain text string to fit within budget_tokens."""
    words = text.split()
    # rough: 1 token ≈ 4 chars ≈ 0.75 words
    max_words = int(budget_tokens * 0.75)
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "\n… [truncated for token budget]"


def sq_num_predict(num_ctx: int, call_type: str) -> int:
    """Return num_predict scaled from num_ctx. call_type: evidence|skeptic|section|repair|ledger."""
    base = {
        "evidence": NUM_PREDICT_EVIDENCE,
        "skeptic": NUM_PREDICT_SKEPTIC,
        "section": NUM_PREDICT_SECTION,
        "repair": NUM_PREDICT_REPAIR,
        "ledger": NUM_PREDICT_LEDGER,
    }.get(call_type, 500)
    # Scale proportionally if user has raised num_ctx
    scale = max(1.0, num_ctx / 8192)
    return min(int(base * scale), num_ctx // 3)
