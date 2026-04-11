# -*- coding: utf-8 -*-
"""
Liquidación de sueldo handler — Wave F

Usage:
  1. User sends /liquidacion
  2. Bot activates liquidacion mode for 10 minutes
  3. Next PDF or photo is parsed with the liquidacion prompt and inserted into
     the `income` table (employer, gross, net, deductions, bonuses, period).

Mode stored in context.user_data["liquidacion_mode"] = {"active": True, "expires_at": ...}
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

LIQUIDACION_MODE_MINUTES = 10


# ── Command handler ────────────────────────────────────────────────────────────

async def handle_liquidacion_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Activate liquidacion mode: next uploaded file will be parsed as a payslip."""
    expires = datetime.now(timezone.utc) + timedelta(minutes=LIQUIDACION_MODE_MINUTES)
    context.user_data["liquidacion_mode"] = {"active": True, "expires_at": expires.isoformat()}
    await update.message.reply_text(
        "📄 Modo liquidación activado. "
        f"Mándame el PDF o foto de tu liquidación de sueldo "
        f"en los próximos {LIQUIDACION_MODE_MINUTES} minutos.\n"
        "Extraeré sueldo bruto, líquido, descuentos (AFP, ISAPRE, impuesto) y bonos. ✅"
    )


# ── Mode detection ─────────────────────────────────────────────────────────────

def is_liquidacion_mode_active(user_data: dict[str, Any]) -> bool:
    """Return True if liquidacion mode is active and not expired."""
    liq = user_data.get("liquidacion_mode")
    if not isinstance(liq, dict) or not liq.get("active"):
        return False
    expires_at_raw = liq.get("expires_at")
    if not expires_at_raw:
        return False
    try:
        expires_at = datetime.fromisoformat(str(expires_at_raw))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < expires_at
    except ValueError:
        return False


def deactivate_liquidacion_mode(user_data: dict[str, Any]) -> None:
    if "liquidacion_mode" in user_data:
        user_data["liquidacion_mode"]["active"] = False


# ── Liquidacion-specific file handlers ────────────────────────────────────────

async def handle_liquidacion_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photo upload in liquidacion mode."""
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

    await message.reply_text("⏳ Procesando tu liquidación…")

    try:
        parsed = claude_service.parse_liquidacion_image(image_base64, user_comment, raw_bytes=file_bytes)
        await _finish_liquidacion(message, context, file_bytes, "liquidacion.jpg", "image/jpeg", parsed)
    except Exception:
        logger.exception("Liquidacion photo parse failed")
        await message.reply_text("❌ No pude procesar la liquidación. Inténtalo de nuevo.")
    finally:
        deactivate_liquidacion_mode(context.user_data)


async def handle_liquidacion_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle document upload in liquidacion mode."""
    message = update.message
    if not message or not message.document:
        return

    document = message.document
    file_name = document.file_name or "liquidacion"
    extension = Path(file_name).suffix.lower()
    telegram_file = await document.get_file()
    file_bytes = bytes(await telegram_file.download_as_bytearray())
    user_comment = (context.user_data.get("last_text_comment") or "").strip()

    await message.reply_text("⏳ Procesando tu liquidación…")

    try:
        if extension == ".pdf":
            text_content = _extract_pdf_text(file_bytes)
            parsed = claude_service.parse_liquidacion_pdf(text_content, user_comment)
        elif extension in {".jpg", ".jpeg", ".png", ".webp"}:
            if image_enhancer.should_enhance(file_bytes):
                file_bytes = image_enhancer.enhance(file_bytes)
            image_base64 = base64.b64encode(file_bytes).decode("utf-8")
            parsed = claude_service.parse_liquidacion_image(image_base64, user_comment, raw_bytes=file_bytes)
        else:
            await message.reply_text("⚠️ Formato no soportado para liquidación. Usa PDF o imagen.")
            return

        await _finish_liquidacion(message, context, file_bytes, file_name,
                                   document.mime_type or "application/octet-stream", parsed)
    except Exception:
        logger.exception("Liquidacion document parse failed: %s", file_name)
        await message.reply_text("❌ No pude procesar la liquidación. Verifica el formato o inténtalo otra vez.")
    finally:
        deactivate_liquidacion_mode(context.user_data)


# ── Shared finish logic ────────────────────────────────────────────────────────

async def _finish_liquidacion(message, context, file_bytes, filename, mime_type, parsed):
    parser_used = parsed.get("parser", "claude")

    # Back up to Drive
    try:
        drive_url = drive_service.upload_file(file_bytes, filename, mime_type)
    except Exception:
        logger.exception("Drive upload failed for liquidacion — continuing")
        drive_url = None

    # Insert into income table
    inserted = supabase_service.insert_income(parsed)

    context.user_data["last_file"] = {
        "type": "liquidacion",
        "filename": filename,
        "drive_url": drive_url,
        "inserted": inserted,
        "parser": parser_used,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }

    employer = parsed.get("employer") or "—"
    period = _format_period(parsed.get("period_start"), parsed.get("period_end"))
    gross = parsed.get("gross_amount")
    net = parsed.get("net_amount")
    gross_str = f"${gross:,.0f}" if isinstance(gross, (int, float)) else "—"
    net_str = f"${net:,.0f}" if isinstance(net, (int, float)) else "—"

    deductions = parsed.get("deductions") or {}
    deduction_lines = "\n".join(
        f"  • {k}: ${v:,.0f}" for k, v in deductions.items() if isinstance(v, (int, float)) and v
    )

    bonuses = parsed.get("bonuses") or {}
    bonus_lines = "\n".join(
        f"  • {k}: ${v:,.0f}" for k, v in bonuses.items() if isinstance(v, (int, float)) and v
    )

    lines = [
        f"✅ Liquidación procesada.",
        f"🏢 {employer}  |  {period}",
        f"💰 Bruto: {gross_str}   Líquido: {net_str}",
    ]
    if deduction_lines:
        lines.append(f"📉 Descuentos:\n{deduction_lines}")
    if bonus_lines:
        lines.append(f"➕ Bonos/asignaciones:\n{bonus_lines}")
    lines.append(f"⚙️ Parser: {parser_used}")

    await message.reply_text("\n".join(lines))


def _extract_pdf_text(file_bytes: bytes) -> str:
    pages: list[str] = []
    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text.strip():
                pages.append(text)
    return "\n\n".join(pages)


def _format_period(start: str | None, end: str | None) -> str:
    if start and end:
        return f"{start} → {end}"
    if start:
        return start
    return "período desconocido"
