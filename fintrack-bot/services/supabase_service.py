import logging
import os
from collections import defaultdict
from typing import Any

from supabase import Client, create_client

logger = logging.getLogger(__name__)

ACCOUNT_MAP = {
    "itau": "PLACEHOLDER_UUID",
    "scotiabank": "PLACEHOLDER_UUID",
    "banco de chile": "PLACEHOLDER_UUID",
    "tenpo": "PLACEHOLDER_UUID",
    "cmr falabella": "PLACEHOLDER_UUID",
}


def _get_client() -> Client:
    url = os.getenv("SUPABASE_URL")
    anon_key = os.getenv("SUPABASE_ANON_KEY")

    if not url or not anon_key:
        raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY must be configured.")

    return create_client(url, anon_key)


def _extract_text(transaction: dict[str, Any], key: str) -> str:
    value = transaction.get(key)
    return str(value).strip() if value is not None else ""


def _infer_account_id(transaction: dict[str, Any]) -> str | None:
    text_blob = " ".join(
        [
            _extract_text(transaction, "merchant"),
            _extract_text(transaction, "description"),
            _extract_text(transaction, "source"),
            _extract_text(transaction, "notes"),
        ]
    ).lower()

    for bank_name, account_id in ACCOUNT_MAP.items():
        if bank_name in text_blob:
            if account_id == "PLACEHOLDER_UUID":
                logger.warning("Account mapping for '%s' still has a placeholder UUID.", bank_name)
                return None
            return account_id

    return None


def insert_transactions(transactions: list) -> int:
    if not transactions:
        return 0

    rows: list[dict[str, Any]] = []
    for tx in transactions:
        if not isinstance(tx, dict):
            logger.warning("Skipping non-dict transaction payload: %r", tx)
            continue

        row = {
            "date": tx.get("date"),
            "description": tx.get("description") or "Sin descripción",
            "amount": tx.get("amount", 0),
            "currency": tx.get("currency") or "CLP",
            "category": tx.get("category") or "other",
            "account_id": _infer_account_id(tx),
            "notes": tx.get("notes"),
            "source": tx.get("merchant") or tx.get("source") or "telegram",
        }
        rows.append(row)

    if not rows:
        return 0

    try:
        client = _get_client()
        client.table("transactions").insert(rows).execute()
        return len(rows)
    except Exception:
        logger.exception("Failed to insert transactions into Supabase")
        raise


def get_transactions(filters: dict | None = None) -> list:
    filters = filters or {}

    try:
        client = _get_client()
        query = client.table("transactions").select("*")

        if filters.get("start_date"):
            query = query.gte("date", filters["start_date"])
        if filters.get("end_date"):
            query = query.lte("date", filters["end_date"])
        if filters.get("category"):
            query = query.eq("category", filters["category"])
        if filters.get("min_amount") is not None:
            query = query.gte("amount", filters["min_amount"])
        if filters.get("max_amount") is not None:
            query = query.lte("amount", filters["max_amount"])

        response = query.order("date", desc=False).execute()
        return response.data or []
    except Exception:
        logger.exception("Failed to fetch transactions from Supabase")
        raise


def get_spending_summary(start_date: str, end_date: str) -> dict:
    transactions = get_transactions(
        {
            "start_date": start_date,
            "end_date": end_date,
        }
    )

    grouped: dict[str, float] = defaultdict(float)
    for tx in transactions:
        amount = tx.get("amount", 0) or 0
        category = tx.get("category") or "other"
        try:
            numeric_amount = float(amount)
        except (TypeError, ValueError):
            logger.warning("Skipping transaction with non-numeric amount: %r", tx)
            continue

        if numeric_amount < 0:
            grouped[category] += abs(numeric_amount)

    total_spending = sum(grouped.values())
    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_spending": total_spending,
        "by_category": dict(grouped),
    }
