# Drive relay

The bots use a Google service account. It can **update** files and create folders in your Drive,
but it **cannot create new files** (service accounts have no Drive storage). New files (a new
month's report, a new master) go through a small Google Apps Script web app that runs as you:
`drive_relay.gs`.

It is set up already ("Bindal Bots Drive Relay" in bhikan.serein@gmail.com). Redo these steps
only if new-file uploads start failing ("relay" errors in Telegram), or to change the token.

## Setup / redo

1. Open <https://script.google.com> > **New project** (or open "Bindal Bots Drive Relay").
2. Paste `drive_relay.gs` into the editor. Replace `PUT_DRIVE_RELAY_TOKEN_HERE` with the value of
   `DRIVE_RELAY_TOKEN` from `.env`. **Do not commit** the file with the real token.
3. **Deploy** > **New deployment** > type **Web app**:
   * Execute as: **Me**
   * Who has access: **Anyone** (the token protects it; "Only myself" makes every call fail
     with 401)
4. **Deploy** > allow access with your Google account > copy the **Web app URL**.
5. Put the URL in `.env` as `DRIVE_RELAY_URL`, and on GitHub:
   `gh secret set DRIVE_RELAY_URL` (paste the URL). If you changed the token, also
   `gh secret set DRIVE_RELAY_TOKEN`.

Editing the code of an existing deployment needs **Deploy > Manage deployments > edit > New
version**, otherwise the old code keeps running.
