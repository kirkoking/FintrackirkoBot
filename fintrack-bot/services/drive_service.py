import logging
import os
from io import BytesIO

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _get_drive_client():
    refresh_token = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN")
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")

    if not (refresh_token and client_id and client_secret):
        raise ValueError(
            "Google Drive OAuth2 not configured. "
            "Set GOOGLE_OAUTH_REFRESH_TOKEN, GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET."
        )

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


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
