from telegram import Update
from telegram.ext import ContextTypes


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if update.message and update.message.text:
        await update.message.reply_text(
            "Hi! I'm Fintrack Bot. Send a receipt photo, a PDF/Excel file, or a message to get started."
        )
