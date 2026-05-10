# -*- coding: utf-8 -*-
"""
CTA (cuenta corriente / cuenta vista) handler — Wave E

Usage:
  1. User sends /cartola
  2. Bot activates CTA mode for 10 minutes
  3. Next PDF or photo is parsed with the CTA prompt (transfer_in/out, counterparts, etc.)

CTA mode stored in context.user_data["cta_mode"] = {"active": True, "expires_at": ...}
"""
import base64
import logging
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import pdfplumber
from telegram import Update
from telegram.ext import ContextTypes

from services import claude_service, drive_service, image_enhancer, supabase_service

logger = logging.getLogger(__name__)

CTA_MODE_MINUTES = 10  # How long CTA mode stays active after /cartola


# ── Command handler ────────────────────────────────────────────────────────────

async def handle_cartola_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Activate CTA mode: next uploaded file will be parsed as cuenta corriente."""
    expires = datetime.now(timezone.utc) + timedelta(minutes=CTA_MODE_MINUTES)
    context.user_data["cta_mode"] = {"active": True, "expires_at": expires.isoformat()}
    await update.message.reply_text(
        "📄 Modo cartola activado. "
        f"Mándame el PDF o foto de tu cartola (cuenta corriente / cuenta vista) "
        f"en los próximos {CTA_MODE_MINUTES} minutos.\n"
        "Extraeré transferencias, abonos, pagos TC y su contraparte. ✅"
    )


# ── Mode detection ─────────────────────────────────────────────────────────────

def is_cta_mode_active(user_data: dict[str, Any]) -> bool:
    """Return True if CTA mode is active and not expired."""
    cta = user_data.get("cta_mode")
    if not isinstance(cta, dict) or not cta.get("active"):
        return False
    expires_at_raw = cta.get("expires_at")
    if not expires_at_raw:
        return False
    try:
        expires_at = datetime.fromisoformat(str(expires_at_raw))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < expires_at
    except ValueError:
        return False


def deactivate_cta_mode(user_data: dict[str, Any]) -> None:
    if "cta_mode" in user_data:
        user_data["cta_mode"]["active"] = False


# ── CTA-specific file handlers ─────────────────────────────────────────────────

async def handle_cta_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photo upload in CTA mode."""
    message = update.message
    if not message or not message.photo:
        return

    photo = message.photo[-1]
    telegram_file = await photo.get_file()
    file_bytes = bytes(await telegram_file.download_as_bytearray())
    user_comment = (context.user_data.get("last_text_comment") or "").strip()

    if image_enhancer.should_enhance(file_bytes):
        file_bytes = image_enhancer.enhance(file_bytes)

    image_base64 = base64.b64encode(file_bytes).decode("utf-8")

    await message.reply_text("⏳ Procesando tu cartola…")

    try:
        parsed = claude_service.parse_cta_image(image_base64, user_comment, raw_bytes=file_bytes)
        await _finish_cta(message, context, file_bytes, "cartola.jpg", "image/jpeg", parsed)
    except Exception:
        logger.exception("CTA photo parse failed")
        await message.reply_text("❌ No pude procesar la cartola. Inténtalo de nuevo.")
    finally:
        deactivate_cta_mode(context.user_data)


async def handle_cta_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle document upload in CTA mode."""
    message = update.message
    if not message or not message.document:
        return

    document = message.document
    file_name = document.file_name or "cartola"
    extension = Path(file_name).suffix.lower()
    telegram_file = await document.get_file()
    file_bytes = bytes(await telegram_file.download_as_bytearray())
    user_comment = (context.user_data.get("last_text_comment") or "").strip()

    await message.reply_text("⏳ Procesando tu cartola…")

    try:
        if extension == ".pdf":
            text_content = _extract_pdf_text(file_bytes)
            parsed = claude_service.parse_cta_pdf(text_content, user_comment)
        elif extension in {".xlsx", ".xls", ".csv"}:
            import pandas as pd
            df = pd.read_csv(BytesIO(file_bytes)) if extension == ".csv" else pd.read_excel(BytesIO(file_bytes))
            text_content = df.to_string(index=False)
            parsed = claude_service.parse_cta_pdf(text_content, user_comment)
        elif extension in {".jpg", ".jpeg", ".png", ".webp"}:
            if image_enhancer.should_enhance(file_bytes):
                file_bytes = image_enhancer.enhance(file_bytes)
            image_base64 = base64.b64encode(file_bytes).decode("utf-8")
            parsed = claude_service.parse_cta_image(image_base64, user_comment, raw_bytes=file_bytes)
        else:
            await message.reply_text("⚠️ Formato no soportado para cartola. Usa PDF, Excel o imagen.")
            return

        await _finish_cta(message, context, file_bytes, file_name,
                          document.mime_type or "application/octet-stream", parsed)
    except Exception:
        logger.exception("CTA document parse failed: %s", file_name)
        await message.reply_text("❌ No pude procesar la cartola. Verifica el formato o inténtalo otra vez.")
    finally:
        deactivate_cta_mode(context.user_data)


# ── Shared finish logic ────────────────────────────────────────────────────────

async def _finish_cta(message, context, file_bytes, filename, mime_type, parsed):
    transactions = parsed.get("transactions", [])
    parser_used = parsed.get("parser", "claude")
    bank_detected = parsed.get("bank_detected")

    # Back up to Drive
    try:
        drive_url = drive_service.upload_file(file_bytes, filename, mime_type)
    except Exception:
        logger.exception("Drive upload failed for CTA — continuing")
        drive_url = None

    # Resolve document-level account_id from bank_detected so every transaction
    # gets the correct checking account without relying on description text matching.
    account_id = supabase_service.resolve_cta_account_id(bank_detected)
    tx_ids = supabase_service.insert_transactions(transactions, default_account_id=account_id)

    context.user_data["last_file"] = {
        "type": "cta",
        "filename": filename,
        "drive_url": drive_url,
        "transactions_count": len(transactions),
        "transaction_ids": tx_ids,
        "parser": parser_used,
        "bank_detected": bank_detected,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }

    # Build summary by transaction type
    type_counts: dict[str, int] = {}
    for tx in transactions:
        t = tx.get("transaction_type", "expense")
        type_counts[t] = type_counts.get(t, 0) + 1

    type_summary = ", ".join(f"{v} {_type_label(k)}" for k, v in sorted(type_counts.items()))
    bank_line = f" · {bank_detected}" if bank_detected else ""
    edit_hint = "\n✏️ ¿Algo mal? Usa /editar para corregirlo." if tx_ids else ""

    await message.reply_text(
        f"✅ Cartola procesada{bank_line}.\n"
        f"📊 {len(transactions)} movimientos ({type_summary}) — guardados: {len(tx_ids)}\n"
        f"⚙️ Parser: {parser_used}{edit_hint}"
    )


def _extract_pdf_text(file_bytes: bytes) -> str:
    pages: list[str] = []
    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text.strip():
                pages.append(text)
    return "\n\n".join(pages)


def _type_label(t: str) -> str:
    return {
        "expense": "gastos",
        "transfer_out": "transferencias enviadas",
        "transfer_in": "abonos recibidos",
        "income": "ingresos",
        "payment": "pagos TC",
        "fees": "comisiones",
    }.get(t, t)
