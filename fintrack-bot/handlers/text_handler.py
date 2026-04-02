import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from services import claude_service, supabase_service

logger = logging.getLogger(__name__)

QUESTION_KEYWORDS = {
    "cuánto",
    "cuanto",
    "gasté",
    "gaste",
    "gasto",
    "spending",
    "total",
    "resumen",
    "categoría",
    "categoria",
    "mes",
}


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    normalized = text.lower()

    # Keep latest free-form comment for file handlers.
    context.user_data["last_text_comment"] = text

    if _is_help_command(normalized):
        await update.message.reply_text(_help_message())
        return

    if _is_summary_command(normalized):
        await _reply_summary(update, days=30)
        return

    if "este mes" in normalized:
        await _reply_month_summary(update)
        return

    if _has_recent_pending_file(context.user_data):
        _store_comment_for_last_file(context.user_data, text)
        await update.message.reply_text("📝 Guardé tu comentario para esas transacciones.")
        return

    if _looks_like_question(normalized):
        await _answer_finance_question(update, text)
        return

    await update.message.reply_text(
        "💬 Comentario guardado. Si quieres, escribe 'resumen', 'este mes' o una pregunta de gastos."
    )


def _is_help_command(normalized_text: str) -> bool:
    return normalized_text in {"ayuda", "help"}


def _is_summary_command(normalized_text: str) -> bool:
    return normalized_text in {"resumen", "summary"}


def _looks_like_question(normalized_text: str) -> bool:
    if "?" in normalized_text:
        return True

    return any(keyword in normalized_text for keyword in QUESTION_KEYWORDS)


def _has_recent_pending_file(user_data: dict[str, Any]) -> bool:
    last_file = user_data.get("last_file")
    if not isinstance(last_file, dict):
        return False

    uploaded_at_raw = last_file.get("uploaded_at")
    if not uploaded_at_raw:
        return False

    try:
        uploaded_at = datetime.fromisoformat(str(uploaded_at_raw))
        if uploaded_at.tzinfo is None:
            uploaded_at = uploaded_at.replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        return (now_utc - uploaded_at) <= timedelta(seconds=60)
    except ValueError:
        logger.warning("Invalid uploaded_at format in context.user_data['last_file']: %r", uploaded_at_raw)
        return False


def _store_comment_for_last_file(user_data: dict[str, Any], comment: str) -> None:
    last_file = user_data.get("last_file")
    if not isinstance(last_file, dict):
        return

    last_file["comment"] = comment
    last_file["commented_at"] = datetime.now(timezone.utc).isoformat()

    transactions = last_file.get("transactions")
    if isinstance(transactions, list) and transactions:
        annotated_transactions = []
        for tx in transactions:
            if not isinstance(tx, dict):
                continue
            tx_copy = dict(tx)
            existing_notes = str(tx_copy.get("notes") or "").strip()
            tx_copy["notes"] = f"{existing_notes} | comentario: {comment}".strip(" |")
            annotated_transactions.append(tx_copy)

        if annotated_transactions:
            try:
                supabase_service.insert_transactions(annotated_transactions)
                last_file["transactions"] = annotated_transactions
                last_file["annotated"] = True
            except Exception:
                logger.exception("Failed to persist annotated transactions")


async def _answer_finance_question(update: Update, question: str) -> None:
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=30)

    try:
        transactions = supabase_service.get_transactions(
            {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            }
        )
        context_data = json.dumps(transactions, ensure_ascii=False, default=str)
        answer = claude_service.answer_finance_question(question, context_data)
        await update.message.reply_text(f"🤖 {answer}")
    except Exception:
        logger.exception("Failed to answer finance question")
        await update.message.reply_text(
            "❌ No pude responder tu pregunta ahora. Inténtalo de nuevo en un momento."
        )


async def _reply_summary(update: Update, days: int) -> None:
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days)

    await _send_summary_message(update, start_date.isoformat(), end_date.isoformat(), title="📊 Resumen")


async def _reply_month_summary(update: Update) -> None:
    today = datetime.now(timezone.utc).date()
    start_date = today.replace(day=1)
    await _send_summary_message(
        update,
        start_date.isoformat(),
        today.isoformat(),
        title="🗓️ Resumen de este mes",
    )


async def _send_summary_message(update: Update, start_date: str, end_date: str, title: str) -> None:
    try:
        summary = supabase_service.get_spending_summary(start_date, end_date)
        by_category = summary.get("by_category", {}) or {}
        total = summary.get("total_spending", 0)

        if not by_category:
            await update.message.reply_text(
                f"{title}\nNo encontré gastos entre {start_date} y {end_date}."
            )
            return

        lines = [f"{title}", f"💸 Total: ${total:,.0f} CLP", "📂 Por categoría:"]
        for category, amount in sorted(by_category.items(), key=lambda item: item[1], reverse=True):
            lines.append(f"• {category}: ${amount:,.0f} CLP")

        await update.message.reply_text("\n".join(lines))
    except Exception:
        logger.exception("Failed to build spending summary")
        await update.message.reply_text(
            "❌ No pude generar tu resumen por ahora. Inténtalo nuevamente."
        )


def _help_message() -> str:
    return (
        "🆘 Ayuda Fintrack Bot\n"
        "• Envíame una boleta/foto o PDF/Excel/CSV para registrar transacciones.\n"
        "• Luego puedes agregar un comentario en texto.\n"
        "• Escribe resumen para ver últimos 30 días.\n"
        "• Escribe este mes para ver el mes actual.\n"
        "• También puedes hacer preguntas como: '¿Cuánto gasté en comida?'"
    )