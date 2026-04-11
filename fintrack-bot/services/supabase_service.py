import logging
import os
from collections import defaultdict
from typing import Any

from supabase import Client, create_client

logger = logging.getLogger(__name__)

# Real account UUIDs from Supabase
ACCOUNT_MAP = {
    "itau":           "0e1f70bf-013a-416d-b6fb-bc55ed30b9f1",
    "itaú":            "0e1f70bf-013a-416d-b6fb-bc55ed30b9f1",
    "scotiabank":     "f5b382e4-4625-4b70-a890-ca44ace192fd",
    "banco de chile": "9636953b-d4cb-4a4a-b193-76796cc9c51d",
    "bdc":            "9636953b-d4cb-4a4a-b193-76796cc9c51d",
    "tenpo":          "f30ac63e-b0e2-4b5f-ad62-fd7a7671ff0b",
    "cmr falabella":  "b3428f73-ae68-48ce-820c-3dcae69d9873",
    "cmr":            "b3428f73-ae68-48ce-820c-3dcae69d9873",
    "falabella":      "b3428f73-ae68-48ce-820c-3dcae69d9873",
}

VALID_CATEGORIES = {
    "food", "transport", "shopping", "health", "entertainment",
    "fitness", "personal_care", "utilities", "housing", "fees",
    "loan_payment", "payment", "other", "travel", "donations",
    "subscriptions", "pets", "education"
}


def _get_client() -> Client:
    url = os.getenv("SUPABASE_URL")
    anon_key = os.getenv("SUPABASE_ANON_KEY")
    if not url or not anon_key:
        raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY must be configured.")
    return create_client(url, anon_key)


def _extract_text(transaction: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = transaction.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    return ""


def _infer_account_id(transaction: dict[str, Any]) -> str | None:
    text_blob = " ".join([
        _extract_text(transaction, "merchant"),
        _extract_text(transaction, "description_clean"),
        _extract_text(transaction, "description_raw"),
        _extract_text(transaction, "description"),
        _extract_text(transaction, "notes"),
    ]).lower()
    for bank_name, account_id in ACCOUNT_MAP.items():
        if bank_name in text_blob:
            return account_id
    return None


def _safe_category(raw: str | None) -> str:
    if not raw:
        return "other"
    slug = str(raw).strip().lower()
    return slug if slug in VALID_CATEGORIES else "other"


def insert_transactions(transactions: list) -> int:
    if not transactions:
        return 0
    rows: list[dict[str, Any]] = []
    for tx in transactions:
        if not isinstance(tx, dict):
            logger.warning("Skipping non-dict transaction: %r", tx)
            continue
        description_raw = _extract_text(
            tx, "description_raw", "description", "merchant"
        ) or "Sin descripción"
        description_clean = _extract_text(
            tx, "description_clean", "merchant", "description_raw", "description"
        ) or description_raw
        row = {
            "date":              tx.get("date"),
            "description_raw":   description_raw,
            "description_clean": description_clean,
            "amount":            tx.get("amount", 0),
            "currency":          tx.get("currency") or "CLP",
            "category_slug":     _safe_category(tx.get("category_slug") or tx.get("category")),
            "account_id":        _infer_account_id(tx),
            "transaction_type":  tx.get("transaction_type") or "expense",
            "notes":             tx.get("notes") or None,
            "counterpart_name":  tx.get("counterpart_name") or None,
            "counterpart_rut":   tx.get("counterpart_rut") or None,
            "counterpart_bank":  tx.get("counterpart_bank") or None,
            "bank_reference":    tx.get("bank_reference") or None,
        }
        rows.append(row)
    if not rows:
        return 0
    try:
        client = _get_client()
        result = client.table("transactions").insert(rows).execute()
        inserted = len(result.data) if result.data else len(rows)
        logger.info("Inserted %d transactions", inserted)
        return inserted
    except Exception:
        logger.exception("Failed to insert transactions into Supabase")
        raise


def get_transactions(filters: dict | None = None) -> list:
    filters = filters or {}
    try:
        client = _get_client()
        query = client.table("transactions").select(
            "id,date,description_clean,amount,currency,category_slug,account_id,notes,transaction_type,receipt_url"
        )
        if filters.get("start_date"):
            query = query.gte("date", filters["start_date"])
        if filters.get("end_date"):
            query = query.lte("date", filters["end_date"])
        if filters.get("category_slug"):
            query = query.eq("category_slug", filters["category_slug"])
        response = query.order("date", desc=True).execute()
        return response.data or []
    except Exception:
        logger.exception("Failed to fetch transactions from Supabase")
        raise


def get_spending_summary(start_date: str, end_date: str) -> dict:
    transactions = get_transactions({"start_date": start_date, "end_date": end_date})
    grouped: dict[str, float] = defaultdict(float)
    for tx in transactions:
        amount = tx.get("amount", 0) or 0
        category = tx.get("category_slug") or "other"
        try:
            numeric_amount = float(amount)
        except (TypeError, ValueError):
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
