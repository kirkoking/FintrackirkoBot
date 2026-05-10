# -*- coding: utf-8 -*-
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from services import claude_service, supabase_service

logger = logging.getLogger(__name__)

EDIT_MODE_TTL_SECONDS = 300  # 5 minutes


def is_edit_mode_active(user_data: dict[str, Any]) -> bool:
    mode = user_data.get("edit_mode")
    if not isinstance(mode, dict) or not mode.get("active"):
        return False
    expires_raw = mode.get("expires_at")
    if not expires_raw:
        return False
    try:
        expires = datetime.fromisoformat(str(expires_raw))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < expires
    except ValueError:
        return False


def deactivate_edit_mode(user_data: dict[str, Any]) -> None:
    mode = user_data.get("edit_mode")
    if isinstance(mode, dict):
        mode["active"] = False


async def handle_edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/editar — shows last uploaded transactions so the user can correct them."""
    message = update.message
    last_file = context.user_data.get("last_file", {})
    tx_ids = last_file.get("transaction_ids", [])

    if not tx_ids:
        await message.reply_text(
            "No tengo transacciones recientes para editar. Sube una boleta primero."
        )
        return

    try:
        txs = supabase_service.get_transactions_by_ids(tx_ids)
    except Exception:
        logger.exception("Failed to fetch transactions for edit")
        await message.reply_text("❌ No pude cargar las transacciones. Intenta de nuevo.")
        return

    if not txs:
        await message.reply_text("No encontré las transacciones guardadas.")
        return

    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=EDIT_MODE_TTL_SECONDS)
    ).isoformat()

    if len(txs) == 1:
        tx = txs[0]
        context.user_data["edit_mode"] = {
            "active": True,
            "expires_at": expires_at,
            "tx_ids": tx_ids,
            "selected_tx_id": tx["id"],
        }
        await message.reply_text(
            f"✏️ Editando:\n{_format_tx(tx)}\n\n"
            "¿Qué corrijo? Algunos ejemplos:\n"
            "• `monto 10000`\n"
            "• `nombre La Tiendita`\n"
            "• `categoría food`\n"
            "• `fecha 2026-05-09`\n\n"
            "Escribe `listo` para terminar o `cancelar` para salir sin más cambios."
        )
    else:
        context.user_data["edit_mode"] = {
            "active": True,
            "expires_at": expires_at,
            "tx_ids": tx_ids,
            "selected_tx_id": None,
        }
        lines = ["✏️ ¿Cuál transacción edito?"]
        for i, tx in enumerate(txs, 1):
            lines.append(f"{i}. {_format_tx(tx)}")
        lines.append("\nResponde con el número (ej: `1`).")
        await message.reply_text("\n".join(lines))


async def handle_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages while edit mode is active."""
    message = update.message
    text = update.message.text.strip()
    normalized = text.lower()

    if normalized in {"cancelar", "cancel", "salir", "listo", "done", "ya", "ok"}:
        deactivate_edit_mode(context.user_data)
        await message.reply_text("✅ Correcciones guardadas.")
        return

    mode = context.user_data.get("edit_mode", {})

    # Step 1: no transaction selected yet — expect a number
    if mode.get("selected_tx_id") is None:
        tx_ids = mode.get("tx_ids", [])
        try:
            index = int(text.strip()) - 1
            if index < 0 or index >= len(tx_ids):
                raise ValueError
        except ValueError:
            await message.reply_text(
                f"Responde con un número del 1 al {len(tx_ids)}."
            )
            return

        selected_id = tx_ids[index]
        mode["selected_tx_id"] = selected_id

        try:
            txs = supabase_service.get_transactions_by_ids([selected_id])
        except Exception:
            logger.exception("Failed to fetch selected transaction for edit")
            await message.reply_text("❌ Error al cargar la transacción.")
            return

        tx = txs[0] if txs else None
        if not tx:
            await message.reply_text("No encontré esa transacción.")
            return

        await message.reply_text(
            f"✏️ Editando:\n{_format_tx(tx)}\n\n"
            "¿Qué corrijo? Ej: `monto 10000`, `nombre McDonald's`, `categoría food`\n"
            "Escribe `listo` para terminar."
        )
        return

    # Step 2: parse and apply the correction
    selected_id = mode["selected_tx_id"]
    try:
        txs = supabase_service.get_transactions_by_ids([selected_id])
    except Exception:
        logger.exception("Failed to fetch transaction for correction")
        await message.reply_text("❌ Error al cargar la transacción.")
        return

    tx = txs[0] if txs else None
    if not tx:
        await message.reply_text("No encontré la transacción. Escribe `cancelar` para salir.")
        deactivate_edit_mode(context.user_data)
        return

    try:
        correction = claude_service.parse_edit_intent(text, tx)
    except Exception:
        logger.exception("parse_edit_intent failed")
        await message.reply_text(
            "❌ No entendí la corrección. Intenta de nuevo o escribe `cancelar`."
        )
        return

    if not correction or correction.get("error"):
        await message.reply_text(
            "No encontré qué corregir. Intenta algo como:\n"
            "• `monto 10000`\n"
            "• `nombre La Tiendita`\n"
            "• `categoría food`"
        )
        return

    field = correction.get("field")
    value = correction.get("value")

    if not field or value is None:
        await message.reply_text("No pude interpretar la corrección. Intenta de nuevo.")
        return

    ok = supabase_service.update_transaction(selected_id, {field: value})
    if not ok:
        await message.reply_text("❌ No pude guardar el cambio. Intenta de nuevo.")
        return

    field_label = {
        "amount": "Monto",
        "description_clean": "Nombre del local",
        "description_raw": "Descripción original",
        "category_slug": "Categoría",
        "date": "Fecha",
        "notes": "Notas",
    }.get(field, field)

    if field == "amount":
        formatted_value = f"${abs(int(value)):,}".replace(",", ".")
    else:
        formatted_value = str(value)

    await message.reply_text(
        f"✅ {field_label} actualizado a: *{formatted_value}*\n\n"
        "¿Corrijo algo más? (o escribe `listo` para terminar)"
    )


def _format_tx(tx: dict) -> str:
    date = tx.get("date", "sin fecha")
    name = tx.get("description_clean") or "sin nombre"
    amount = tx.get("amount", 0) or 0
    currency = tx.get("currency") or "CLP"
    category = tx.get("category_slug") or "other"
    formatted_amount = f"${abs(int(amount)):,}".replace(",", ".")
    sign = "-" if amount < 0 else "+"
    return f"{date} | {name} | {sign}{formatted_amount} {currency} | {category}"
