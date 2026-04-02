from telegram import Update
from telegram.ext import ContextTypes


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if update.message:
        await update.message.reply_text(
            "📷 Photo received. I'll process this financial image shortly."
        )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if update.message and update.message.document:
        file_name = update.message.document.file_name or "document"
        await update.message.reply_text(
            f"📄 Received {file_name}. I'll analyze this file shortly."
        )
