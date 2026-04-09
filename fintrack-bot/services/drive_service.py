import json
import logging
import os
from io import BytesIO

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _get_drive_client():
    """
    Supports two env var patterns:
    - GOOGLE_SERVICE_ACCOUNT_JSON: the full JSON content as a string (for Render/cloud)
    - GOOGLE_SERVICE_ACCOUNT_PATH: a file path (for local dev)
    """
    json_content = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    json_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH")

    if json_content:
        # Cloud deployment: JSON string stored as env var
        info = json.loads(json_content)
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )
    elif json_path:
        # Local dev: JSON file on disk
        credentials = service_account.Credentials.from_service_account_file(
            json_path, scopes=SCOPES
        )
    else:
        raise ValueError(
            "Google Drive not configured. "
            "Set GOOGLE_SERVICE_ACCOUNT_JSON (JSON string) or "
            "GOOGLE_SERVICE_ACCOUNT_PATH (file path)."
        )

    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def upload_file(file_bytes: bytes, filename: str, mimetype: str) -> str:
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    if not folder_id:
        raise ValueError("GOOGLE_DRIVE_FOLDER_ID is not configured.")

    service = _get_drive_client()

    metadata = {"name": filename, "parents": [folder_id]}
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
