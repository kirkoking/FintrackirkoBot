import logging
import os
from collections import defaultdict
from typing import Any

from supabase import Client, create_client

logger = logging.getLogger(__name__)

# Real account UUIDs from Supabase — credit cards / general accounts
ACCOUNT_MAP = {
    "itau":           "0e1f70bf-013a-416d-b6fb-bc55ed30b9f1",
    "itaú":           "0e1f70bf-013a-416d-b6fb-bc55ed30b9f1",
    "scotiabank":     "f5b382e4-4625-4b70-a890-ca44ace192fd",
    "banco de chile": "9636953b-d4cb-4a4a-b193-76796cc9c51d",
    "bdc":            "9636953b-d4cb-4a4a-b193-76796cc9c51d",
    "tenpo":          "f30ac63e-b0e2-4b5f-ad62-fd7a7671ff0b",
    "cmr falabella":  "b3428f73-ae68-48ce-820c-3dcae69d9873",
    "cmr":            "b3428f73-ae68-48ce-820c-3dcae69d9873",
    "falabella":      "b3428f73-ae68-48ce-820c-3dcae69d9873",
}

# CTA (cuenta corriente / cuenta vista) account UUIDs — keyed by bank_detected value
# from the CTA parser (lower-cased substring match)
CTA_ACCOUNT_MAP = {
    "itaú":          "7270f43c-a0f8-49b7-bef4-2371f20aa75c",
    "itau":          "7270f43c-a0f8-49b7-bef4-2371f20aa75c",
    "tenpo":         "c01d4b83-201d-4f49-bdf4-ca9e28d1ae9f",
    "mercado pago":  "f0b03168-2fa2-4edc-8779-c4ab1d17d350",
    "banco de chile": "e7627b0a-679d-4b3e-b1b6-70b819c9c469",
    "bdc":            "e7627b0a-679d-4b3e-b1b6-70b819c9c469",
    "scotiabank":     "3b793c1f-906b-4469-a52c-3a6e7f960285",
}


def resolve_cta_account_id(bank_detected: str | None) -> str | None:
    """Return the checking account UUID for a CTA bank_detected string, or None."""
    if not bank_detected:
        return None
    key = bank_detected.strip().lower()
    for bank_name, account_id in CTA_ACCOUNT_MAP.items():
        if bank_name in key or key in bank_name:
            return account_id
    return None

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


def insert_transactions(transactions: list, default_account_id: str | None = None) -> list[str]:
    """Insert transactions into Supabase. Returns list of inserted row IDs.

    default_account_id: used for CTA cartola ingests where the account is known
    at the document level (bank_detected) rather than per-transaction.
    Falls back to _infer_account_id() when not supplied.
    """
    if not transactions:
        return []
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
        account_id = default_account_id or _infer_account_id(tx)
        row = {
            "date":              tx.get("date"),
            "description_raw":   description_raw,
            "description_clean": description_clean,
            "amount":            tx.get("amount", 0),
            "currency":          tx.get("currency") or "CLP",
            "category_slug":     _safe_category(tx.get("category_slug") or tx.get("category")),
            "account_id":        account_id,
            "transaction_type":  tx.get("transaction_type") or "expense",
            "notes":             tx.get("notes") or None,
            "counterpart_name":  tx.get("counterpart_name") or None,
            "counterpart_rut":   tx.get("counterpart_rut") or None,
            "counterpart_bank":  tx.get("counterpart_bank") or None,
            "bank_reference":    tx.get("bank_reference") or None,
        }
        rows.append(row)
    if not rows:
        return []
    try:
        client = _get_client()
        result = client.table("transactions").insert(rows).execute()
        ids = [row["id"] for row in (result.data or []) if row.get("id")]
        logger.info("Inserted %d transactions", len(ids))
        return ids
    except Exception:
        logger.exception("Failed to insert transactions into Supabase")
        raise


def get_transactions_by_ids(ids: list[str]) -> list[dict]:
    """Fetch full transaction rows by a list of UUIDs, ordered by date."""
    if not ids:
        return []
    try:
        client = _get_client()
        response = (
            client.table("transactions")
            .select(
                "id,date,description_clean,description_raw,amount,currency,"
                "category_slug,account_id,notes,transaction_type"
            )
            .in_("id", ids)
            .order("date", desc=False)
            .execute()
        )
        return response.data or []
    except Exception:
        logger.exception("Failed to fetch transactions by IDs")
        raise


def update_transaction(tx_id: str, fields: dict) -> bool:
    """Partial update a single transaction row. Returns True on success."""
    if not fields:
        return False
    try:
        client = _get_client()
        client.table("transactions").update(fields).eq("id", tx_id).execute()
        logger.info("Updated transaction %s: %r", tx_id, fields)
        return True
    except Exception:
        logger.exception("Failed to update transaction %s", tx_id)
        return False


def insert_income(parsed: dict) -> bool:
    """Insert a parsed liquidación de sueldo into the income table.
    Returns True on success, False on failure.
    """
    if not parsed:
        return False
    row = {
        "date":         parsed.get("date"),
        "employer":     parsed.get("employer"),
        "gross_amount": parsed.get("gross_amount"),
        "net_amount":   parsed.get("net_amount"),
        "currency":     parsed.get("currency") or "CLP",
        "period_start": parsed.get("period_start"),
        "period_end":   parsed.get("period_end"),
        "deductions":   parsed.get("deductions") or {},
        "bonuses":      parsed.get("bonuses") or {},
        "raw_data":     parsed.get("raw_data") or {},
        "notes":        parsed.get("notes") or None,
    }
    try:
        client = _get_client()
        client.table("income").insert(row).execute()
        logger.info("Inserted income record: employer=%s date=%s net=%s",
                    row["employer"], row["date"], row["net_amount"])
        return True
    except Exception:
        logger.exception("Failed to insert income record into Supabase")
        return False


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
