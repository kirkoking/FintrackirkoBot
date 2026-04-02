import logging
import os

from dotenv import load_dotenv
from telegram.ext import Application, MessageHandler, filters

from handlers import file_handler, text_handler


logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


document_filter = (
    filters.Document.PDF
    | filters.Document.FileExtension("xls")
    | filters.Document.FileExtension("xlsx")
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
    application = build_application()
    logger.info("Starting Fintrack Bot polling loop")
    application.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
