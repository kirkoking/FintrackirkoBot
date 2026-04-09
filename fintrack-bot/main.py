import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from telegram.ext import Application, MessageHandler, filters

from handlers import file_handler, text_handler


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

    application = Application.builder().token(token).build()

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
    application.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
