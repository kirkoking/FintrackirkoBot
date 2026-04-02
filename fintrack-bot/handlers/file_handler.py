import base64
import logging
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import pdfplumber
from telegram import Update
from telegram.ext import ContextTypes

from services import claude_service, drive_service, supabase_service

logger = logging.getLogger(__name__)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.photo:
        return

    photo = message.photo[-1]
    telegram_file = await photo.get_file()
    file_bytes = bytes(await telegram_file.download_as_bytearray())

    image_base64 = base64.b64encode(file_bytes).decode("utf-8")
    user_comment = (context.user_data.get("last_text_comment") or "").strip()

    try:
        parsed_result = claude_service.parse_image(image_base64, user_comment)
        transactions = parsed_result.get("transactions", [])

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"telegram_photo_{message.from_user.id}_{timestamp}.jpg"
        drive_url = drive_service.upload_file(file_bytes, filename, "image/jpeg")
        inserted_count = supabase_service.insert_transactions(transactions)

        context.user_data["last_file"] = {
            "type": "photo",
            "filename": filename,
            "drive_url": drive_url,
            "transactions_count": len(transactions),
            "uploaded_at": datetime.utcnow().isoformat(),
        }

        summary_items = _summarize_transactions(transactions)
        await message.reply_text(
            "✅ Recibí tu boleta. "
            f"Encontré {len(transactions)} transacciones (guardadas: {inserted_count}).\n"
            f"{summary_items}"
        )
    except Exception:
        logger.exception("Failed to process photo message")
        await message.reply_text(
            "❌ No pude procesar la imagen en este momento. Intenta nuevamente en unos minutos."
        )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.document:
        return

    document = message.document
    file_name = document.file_name or "document"
    extension = Path(file_name).suffix.lower()

    telegram_file = await document.get_file()
    file_bytes = bytes(await telegram_file.download_as_bytearray())

    user_comment = (context.user_data.get("last_text_comment") or "").strip()

    try:
        if extension == ".pdf":
            text_content = _extract_pdf_text(file_bytes)
            parsed_result = claude_service.parse_pdf_text(text_content, user_comment)
        elif extension in {".xlsx", ".xls", ".csv"}:
            text_content = _extract_sheet_text(file_bytes, extension)
            parsed_result = claude_service.parse_excel_text(text_content, user_comment)
        else:
            await message.reply_text(
                "⚠️ Formato no soportado. Envía PDF, XLSX, XLS o CSV."
            )
            return

        transactions = parsed_result.get("transactions", [])
        drive_url = drive_service.upload_file(
            file_bytes,
            file_name,
            document.mime_type or "application/octet-stream",
        )
        inserted_count = supabase_service.insert_transactions(transactions)

        context.user_data["last_file"] = {
            "type": "document",
            "filename": file_name,
            "extension": extension,
            "drive_url": drive_url,
            "transactions_count": len(transactions),
            "uploaded_at": datetime.utcnow().isoformat(),
        }

        summary_items = _summarize_transactions(transactions)
        await message.reply_text(
            "✅ Recibí tu documento. "
            f"Encontré {len(transactions)} transacciones (guardadas: {inserted_count}).\n"
            f"{summary_items}"
        )
    except Exception:
        logger.exception("Failed to process document message: %s", file_name)
        await message.reply_text(
            "❌ No pude procesar el documento. Verifica el formato o inténtalo otra vez."
        )


def _extract_pdf_text(file_bytes: bytes) -> str:
    pages: list[str] = []
    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text.strip():
                pages.append(text)
    return "\n\n".join(pages)


def _extract_sheet_text(file_bytes: bytes, extension: str) -> str:
    if extension == ".csv":
        dataframe = pd.read_csv(BytesIO(file_bytes))
        return dataframe.to_string(index=False)

    sheet_map = pd.read_excel(BytesIO(file_bytes), sheet_name=None)
    chunks: list[str] = []
    for sheet_name, dataframe in sheet_map.items():
        rendered = dataframe.to_string(index=False)
        chunks.append(f"### Sheet: {sheet_name}\n{rendered}")

    return "\n\n".join(chunks)


def _summarize_transactions(transactions: list[dict[str, Any]]) -> str:
    if not transactions:
        return "No se detectaron transacciones válidas."

    snippets = []
    for tx in transactions[:5]:
        date = tx.get("date", "sin fecha")
        description = tx.get("description") or tx.get("merchant") or "sin descripción"
        amount = tx.get("amount", "?")
        currency = tx.get("currency") or "CLP"
        snippets.append(f"• {date} | {description} | {amount} {currency}")

    if len(transactions) > 5:
        snippets.append(f"… y {len(transactions) - 5} más")

    return "\n".join(snippets)
