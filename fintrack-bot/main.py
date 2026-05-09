import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from handlers import file_handler, text_handler, cta_handler, liquidacion_handler


logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ── Health check HTTP server for Render ──────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass  # Silence access logs


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info("Health check server running on port %s", port)
    server.serve_forever()
# ─────────────────────────────────────────────────────────────────────────────


document_filter = (
    filters.Document.PDF
    | filters.Document.FileExtension("xls")
    | filters.Document.FileExtension("xlsx")
    | filters.Document.FileExtension("csv")
)


def build_application() -> Application:
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is missing. Set it in the .env file.")

    # Warn loudly at startup if AI keys are missing — so the first log line points
    # at the real problem instead of users seeing generic "try again later" errors.
    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.warning("ANTHROPIC_API_KEY is missing — Claude fallback will fail with 401")
    if not os.getenv("GOOGLE_AI_API_KEY"):
        logger.warning("GOOGLE_AI_API_KEY is missing — Gemini parser disabled, every request will hit Claude")

    application = Application.builder().token(token).build()

    # /cartola — activates CTA mode for the next file upload
    application.add_handler(CommandHandler("cartola", cta_handler.handle_cartola_command))

    # /liquidacion — activates liquidacion mode for the next file upload
    application.add_handler(CommandHandler("liquidacion", liquidacion_handler.handle_liquidacion_command))

    # File handlers — CTA / liquidacion modes take priority when active
    application.add_handler(MessageHandler(filters.PHOTO, file_handler.handle_photo))
    application.add_handler(MessageHandler(document_filter, file_handler.handle_document))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler.handle_text)
    )

    return application


def main() -> None:
    # Start health check server in background thread so Render is happy
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()

    application = build_application()
    logger.info("Starting Fintrack Bot polling loop")
    # drop_pending_updates=True ensures this instance wins if another is running
    application.run_polling(
        allowed_updates=["message"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
