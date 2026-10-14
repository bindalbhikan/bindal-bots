"""Files kept on Google Drive, read and written through the Drive API.

The master is updated in place (same file id, same link), so Drive for desktop
syncs the new version down to G:\\My Drive like any other edit, and Drive keeps
the previous versions under "Manage versions".

The service account needs Editor access to every workbook a bot writes and
Viewer access to the lookup sheets (share them with the client_email from the
key JSON). upload() only replaces a file this run downloaded, and refuses when
someone changed it on Drive in the meantime.
"""
from __future__ import annotations

import logging
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession

from .gauth import credentials

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]
API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FIELDS = "id,name,mimeType,modifiedTime,md5Checksum,size,webViewLink,capabilities/canEdit"


class DriveError(RuntimeError):
    pass


class DriveFile:
    def __init__(self, file_id: str, http: AuthorizedSession | None = None):
        self.file_id = file_id
        self.http = http or AuthorizedSession(credentials(SCOPES))
        self.meta: dict = {}

    def _check(self, r, what: str):
        if r.status_code == 404:
            raise DriveError(f"Drive file {self.file_id} not found - is it shared with the service account?")
        if not r.ok:
            raise DriveError(f"Drive {what} failed: HTTP {r.status_code} {r.text[:300]}")
        return r

    def metadata(self) -> dict:
        r = self.http.get(f"{API}/{self.file_id}", timeout=60,
                          params={"fields": FIELDS, "supportsAllDrives": "true"})
        return self._check(r, "metadata").json()

    @property
    def name(self) -> str:
        return self.meta.get("name", self.file_id)

    @property
    def url(self) -> str:
        return self.meta.get("webViewLink", f"https://drive.google.com/file/d/{self.file_id}/view")

    def download(self, dest: Path, need_edit: bool = False) -> Path:
        self.meta = self.metadata()
        if self.meta.get("mimeType") != XLSX:
            raise DriveError(f"{self.name} is {self.meta.get('mimeType')}, expected an .xlsx file")
        if need_edit and not self.meta.get("capabilities", {}).get("canEdit"):
            raise DriveError(f"Service account can read but not edit {self.name} - share it as Editor")
        r = self.http.get(f"{API}/{self.file_id}", timeout=300,
                          params={"alt": "media", "supportsAllDrives": "true"})
        self._check(r, "download")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        log.info("Downloaded %s from Drive (%s bytes, modified %s)",
                 self.name, len(r.content), self.meta.get("modifiedTime"))
        return dest

    def upload(self, src: Path) -> None:
        """Replace the file's content. Refuses if someone changed it since download()."""
        if not self.meta:
            raise DriveError(f"{self.name}: download() it before upload(), so a change made on Drive "
                             "during the run is never overwritten")
        now = self.metadata()
        if now.get("md5Checksum") != self.meta.get("md5Checksum"):
            raise DriveError(f"{now.get('name')} was changed on Drive during the run "
                             f"(modified {now.get('modifiedTime')}); not overwriting it. Run again.")
        r = self.http.patch(f"{UPLOAD}/{self.file_id}", data=src.read_bytes(), timeout=300,
                            params={"uploadType": "media", "supportsAllDrives": "true", "fields": FIELDS},
                            headers={"Content-Type": XLSX})
        self.meta = self._check(r, "upload").json()
        log.info("Uploaded %s to Drive (modified %s)", self.name, self.meta.get("modifiedTime"))
