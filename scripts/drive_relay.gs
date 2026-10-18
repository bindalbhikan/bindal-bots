// Bindal bots: Drive relay (Google Apps Script web app, runs as the Drive owner).
//
// The bots use a Google service account, and service accounts have no Drive storage of their
// own: they can update files the owner made, but cannot create new ones. The bots send new
// files here (with the secret token) and this script, running as you, saves them in your Drive.
//
// Setup (once): script.google.com > New project > paste this file > put the token from
// DRIVE_RELAY_TOKEN in .env below > Deploy > New deployment > Web app, Execute as: Me,
// Who has access: Anyone > Deploy > allow access > copy the Web app URL into DRIVE_RELAY_URL.

const TOKEN = "PUT_DRIVE_RELAY_TOKEN_HERE";

function doPost(e) {
  let req;
  try {
    req = JSON.parse(e.postData.contents);
  } catch (err) {
    return reply({ ok: false, error: "not JSON" });
  }
  if (!req || req.token !== TOKEN) return reply({ ok: false, error: "forbidden" });
  try {
    const folder = DriveApp.getFolderById(req.folderId);
    const blob = Utilities.newBlob(Utilities.base64Decode(req.data), req.mimeType, req.name);
    const existing = folder.getFilesByName(req.name);
    if (existing.hasNext()) {
      // same file, same id and link: new content through the Drive API as you
      const file = existing.next();
      const url = "https://www.googleapis.com/upload/drive/v3/files/" + file.getId() +
        "?uploadType=media&supportsAllDrives=true";
      const r = UrlFetchApp.fetch(url, {
        method: "patch", contentType: req.mimeType, payload: blob.getBytes(), muteHttpExceptions: true,
        headers: { Authorization: "Bearer " + ScriptApp.getOAuthToken() },
      });
      if (r.getResponseCode() >= 300) return reply({ ok: false, error: r.getContentText().slice(0, 300) });
      return reply({ ok: true, id: file.getId(), updated: true });
    }
    const file = folder.createFile(blob);
    return reply({ ok: true, id: file.getId(), created: true });
  } catch (err) {
    return reply({ ok: false, error: String(err) });
  }
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
