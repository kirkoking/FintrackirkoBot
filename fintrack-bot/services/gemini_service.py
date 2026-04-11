"""
Gemini parser service — Wave D.1
Primary parser: Gemini 2.5 Flash (free tier, fast).
Returns None on any failure so caller can fall back to Claude.
"""

import json
import logging
import os
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# Lazy-import so the bot still starts even if google-generativeai isn't installed
try:
    import google.generativeai as genai
    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False
    logger.warning("google-generativeai not installed — Gemini parser unavailable, Claude will handle everything")


MODEL = "gemini-2.5-flash-preview-04-17"

_SYSTEM_PROMPT = (
    "You are a financial data extractor for a Chilean user. "
    "Extract ALL transactions from the provided content. "
    "Return ONLY a valid JSON object with a 'transactions' key containing an array. "
    "Each transaction must have: "
    "date (YYYY-MM-DD), description_raw (original text), description_clean (normalized merchant name), "
    "amount (negative=expense/charge, positive=income/credit, numeric), "
    "currency (default 'CLP'), "
    "category_slug (one of: food, transport, shopping, health, fitness, entertainment, "
    "personal_care, housing, utilities, loan_payment, fees, payment, other, travel, "
    "donations, subscriptions, pets, education), "
    "transaction_type (expense, transfer_out, transfer_in, income, payment), "
    "merchant (clean merchant name or null). "
    "Today's date: {today}. "
    "If you cannot confidently extract transactions, return {\"transactions\": []}. "
    "NEVER return anything other than the JSON object."
)


def _get_model():
    """Initialize Gemini client. Returns None if unavailable."""
    if not _GENAI_AVAILABLE:
        return None
    api_key = os.getenv("GOOGLE_AI_API_KEY")
    if not api_key:
        logger.warning("GOOGLE_AI_API_KEY not set — Gemini unavailable")
        return None
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(MODEL)


def _build_prompt(source_hint: str) -> str:
    return _SYSTEM_PROMPT.replace("{today}", date.today().isoformat()) + f"\n\nSource: {source_hint}."


def _strip_fences(text: str) -> str:
    """Remove markdown code fences from a Gemini response."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return cleaned


def _parse_response(text: str) -> list[dict[str, Any]] | None:
    """Parse and validate a Gemini JSON response that contains a 'transactions' array.
    Returns None if invalid so the caller falls back to Claude."""
    try:
        parsed = json.loads(_strip_fences(text))
    except json.JSONDecodeError:
        logger.warning("Gemini returned invalid JSON: %.200s", text)
        return None

    if not isinstance(parsed, dict) or "transactions" not in parsed:
        logger.warning("Gemini response missing 'transactions' key")
        return None

    txs = parsed["transactions"]
    if not isinstance(txs, list):
        logger.warning("Gemini 'transactions' is not a list")
        return None

    if not txs:
        return []  # Empty is valid — no transactions found

    # Quality check: reject if >30% of transactions are missing critical fields
    bad = sum(1 for t in txs if not t.get("date") or t.get("amount") is None)
    if bad / len(txs) > 0.3:
        logger.warning("Gemini response quality too low (%d/%d missing date/amount) — falling back", bad, len(txs))
        return None

    return txs


def _parse_response_object(text: str) -> dict[str, Any] | None:
    """Parse a Gemini JSON response that returns a plain object (e.g. liquidacion).
    Returns None on failure so caller falls back to Claude."""
    try:
        parsed = json.loads(_strip_fences(text))
    except json.JSONDecodeError:
        logger.warning("Gemini returned invalid JSON (object): %.200s", text)
        return None
    if not isinstance(parsed, dict):
        logger.warning("Gemini object response is not a dict")
        return None
    return parsed


def parse_image(
    image_bytes: bytes,
    user_comment: str = "",
    system_prompt_override: str | None = None,
    expect_object: bool = False,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """
    Try to parse an image with Gemini.
    expect_object=False (default): returns list of transaction dicts, or None on failure.
    expect_object=True: returns a plain dict (e.g. liquidacion schema), or None on failure.
    """
    model = _get_model()
    if model is None:
        return None

    try:
        import io
        import PIL.Image
        pil_image = PIL.Image.open(io.BytesIO(image_bytes))

        prompt = system_prompt_override or _build_prompt("receipt or boleta image")
        parts: list[Any] = [prompt, pil_image]
        if user_comment:
            parts.append(f"User context: {user_comment}")

        response = model.generate_content(parts)
        if expect_object:
            return _parse_response_object(response.text)
        return _parse_response(response.text)
    except Exception as exc:
        logger.warning("Gemini image parse failed: %s — falling back to Claude", exc)
        return None


def parse_text(
    text: str,
    source_hint: str,
    user_comment: str = "",
    system_prompt_override: str | None = None,
    expect_object: bool = False,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """
    Try to parse text content with Gemini.
    expect_object=False (default): returns list of transaction dicts, or None on failure.
    expect_object=True: returns a plain dict (e.g. liquidacion schema), or None on failure.
    """
    model = _get_model()
    if model is None:
        return None

    try:
        prompt = system_prompt_override or _build_prompt(source_hint)
        content = f"{prompt}\n\nContent to parse:\n{text}"
        if user_comment:
            content += f"\n\nUser context: {user_comment}"

        response = model.generate_content(content)
        if expect_object:
            return _parse_response_object(response.text)
        return _parse_response(response.text)
    except Exception as exc:
        logger.warning("Gemini text parse failed (%s): %s — falling back to Claude", source_hint, exc)
        return None
