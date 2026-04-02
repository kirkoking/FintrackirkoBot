import logging
import os
from io import BytesIO

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _get_drive_client():
    service_account_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not service_account_path:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is not configured.")

    credentials = service_account.Credentials.from_service_account_file(
        service_account_path,
        scopes=SCOPES,
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def upload_file(file_bytes: bytes, filename: str, mimetype: str) -> str:
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    if not folder_id:
        raise ValueError("GOOGLE_DRIVE_FOLDER_ID is not configured.")

    try:
        service = _get_drive_client()

        metadata = {
            "name": filename,
            "parents": [folder_id],
        }
        media = MediaIoBaseUpload(BytesIO(file_bytes), mimetype=mimetype, resumable=False)

        created = (
            service.files()
            .create(body=metadata, media_body=media, fields="id,webViewLink,webContentLink")
            .execute()
        )

        file_id = created["id"]

        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()

        return (
            created.get("webViewLink")
            or created.get("webContentLink")
            or f"https://drive.google.com/file/d/{file_id}/view"
        )
    except Exception:
        logger.exception("Failed to upload file to Google Drive")
        raise
