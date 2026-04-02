import json
import logging
import os
from datetime import date
from typing import Any

from anthropic import Anthropic


logger = logging.getLogger(__name__)

MODEL_NAME = "claude-sonnet-4-20250514"

TRANSACTION_SCHEMA_PROMPT = (
    "You are a financial data extractor for a Chilean user. "
    "Extract all transactions from the image. "
    "Return ONLY a valid JSON array of transactions. "
    "Each transaction: {date, description, amount (negative=expense, positive=income), "
    "currency (default CLP), "
    "category (food/transport/shopping/health/fitness/entertainment/personal_care/"
    "housing/utilities/loan_payment/fees/payment/other), "
    "merchant, notes}. "
    "Use the user comment for additional context. Today's date: {today}"
)


def _get_client() -> Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is missing. Set it in the environment.")
    return Anthropic(api_key=api_key)


def _extract_text_content(response: Any) -> str:
    chunks: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            chunks.append(getattr(block, "text", ""))
    text = "\n".join(chunks).strip()
    if not text:
        raise ValueError("Claude response did not include text content.")
    return text


def _parse_json_array(raw_text: str) -> list[dict[str, Any]]:
    cleaned = raw_text.strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("Failed to decode Claude JSON output: %s", raw_text)
        raise ValueError("Claude returned invalid JSON.") from exc

    if not isinstance(parsed, list):
        raise ValueError("Claude output must be a JSON array of transactions.")

    return parsed


def _build_extraction_prompt(source_hint: str) -> str:
    return (
        f"{TRANSACTION_SCHEMA_PROMPT.format(today=date.today().isoformat())}\n\n"
        f"Source type: {source_hint}."
    )


def _call_text_extraction(source_hint: str, text: str, user_comment: str = "") -> list[dict[str, Any]]:
    client = _get_client()

    system_prompt = _build_extraction_prompt(source_hint)
    user_parts = [f"Extract transactions from this {source_hint}:\n{text}"]
    if user_comment:
        user_parts.append(f"User comment/context: {user_comment}")

    try:
        response = client.messages.create(
            model=MODEL_NAME,
            max_tokens=2000,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": "\n\n".join(user_parts),
                }
            ],
        )
    except Exception as exc:
        logger.exception("Claude API error while parsing %s", source_hint)
        raise RuntimeError("Failed to parse data with Claude API.") from exc

    return _parse_json_array(_extract_text_content(response))


def parse_image(base64_image: str, user_comment: str = "") -> dict:
    try:
        client = _get_client()
        system_prompt = _build_extraction_prompt("receipt or boleta image")

        normalized_base64 = base64_image.strip()
        if normalized_base64.startswith("data:") and "base64," in normalized_base64:
            normalized_base64 = normalized_base64.split("base64,", 1)[1].strip()

        image_payload = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": normalized_base64,
            },
        }

        user_blocks: list[dict[str, Any]] = [image_payload]
        if user_comment:
            user_blocks.append({"type": "text", "text": f"User comment/context: {user_comment}"})

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_blocks}],
        )

        return {"transactions": _parse_json_array(_extract_text_content(response))}
    except Exception as exc:
        logger.exception("Failed to parse image with Claude. Details: %s", exc)
        raise RuntimeError(f"Failed to parse image with Claude API: {exc}") from exc


def parse_pdf_text(text: str, user_comment: str = "") -> dict:
    transactions = _call_text_extraction(
        "Chilean bank statement (estado de cuenta)", text, user_comment
    )
    return {"transactions": transactions}


def parse_excel_text(text: str, user_comment: str = "") -> dict:
    transactions = _call_text_extraction("Excel/CSV transaction data", text, user_comment)
    return {"transactions": transactions}


def answer_finance_question(question: str, context_data: str) -> str:
    client = _get_client()

    system_prompt = (
        "You are a personal finance assistant for a Chilean user. "
        "Answer the question based on the transaction data provided. "
        "Be concise and helpful. Always respond in Spanish."
    )

    try:
        response = client.messages.create(
            model=MODEL_NAME,
            max_tokens=700,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Pregunta del usuario:\n{question}\n\n"
                        f"Datos de transacciones (JSON):\n{context_data}"
                    ),
                }
            ],
        )
    except Exception as exc:
        logger.exception("Claude API error while answering finance question")
        raise RuntimeError("Failed to answer finance question with Claude API.") from exc

    return _extract_text_content(response)
