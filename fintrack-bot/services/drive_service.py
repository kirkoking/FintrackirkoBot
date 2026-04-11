from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import os

def get_drive_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ['GOOGLE_OAUTH_REFRESH_TOKEN'],
        client_id=os.environ['GOOGLE_OAUTH_CLIENT_ID'],
        client_secret=os.environ['GOOGLE_OAUTH_CLIENT_SECRET'],
        token_uri='https://oauth2.googleapis.com/token'
    )
    if creds.expired:
        creds.refresh(Request())
    return build('drive', 'v3', credentials=creds)

def upload_file(file_path, file_name, folder_id):
    service = get_drive_service()
    file_metadata = {
        'name': file_name,
        'parents': [folder_id]
    }
    media = MediaFileUpload(file_path, resumable=True)
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id'
    ).execute()
    return file.get('id')
