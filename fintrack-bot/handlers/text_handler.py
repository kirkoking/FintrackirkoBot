from telegram import Update
from telegram.ext import ContextTypes


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    context.user_data["last_text_comment"] = update.message.text.strip()

    await update.message.reply_text(
        "✅ Comentario guardado. Ahora envíame una boleta o cartola y lo usaré como contexto."
    )