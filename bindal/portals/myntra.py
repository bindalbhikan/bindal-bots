"""Myntra partner portal: fetch the Seller_Orders_Report with a saved login session.

The portal login has a reCAPTCHA, so the bot never logs in. Someone logs in by hand in
Chrome and copies the login cookies (scripts/myntra/myntra_session.py saves them); the bot reuses
that session and, after each run, saves the cookies the portal renewed, which keeps it
alive. Telegram is told when it has expired. Where the session comes from, first found wins:
  1. the Telegram worker (KV myntra_session), read through /api/myntra-session/read
     (DASHBOARD_TOKEN): written by scripts/myntra/myntra_session.py and by every run
  2. MYNTRA_SESSION_JSON, or the file MYNTRA_SESSION_FILE (default myntra_session.json)

The session JSON may be:
  * {"savedAt": ..., "cookies": [{name, value, domain, expires, httpOnly, ...}]}
  * {"cookie": "a=1; b=2"} or the plain Cookie header text, or a whole "Copy as cURL"
    command from Chrome DevTools (no expiry date is known then).

Report flow, as the portal page does it (Reports > Operational Reports):
  POST /api/partners/report/jobs/schedule  {reportId: 4, queryParams: {...}}
  GET  /api/partners/report/jobs/search    until the job is COMPLETED -> successFile (CSV URL)
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

API = "https://partnersapi.myntrainfo.com/api/partners"
PORTAL = "https://partners.myntrainfo.com"
# The three Myntra seller accounts, each with its own portal login (partner id)
ACCOUNTS = {"MAIN": 19170, "DBCC": 32207, "FMCC": 35426}
REPORT_ID = 4                       # Seller_Orders_Report
RETURNS_REPORT_ID = 12              # Seller_Returns_Report (same queryParams; one job = max 1 month)
# After a report is requested the portal refuses the same report for 15 minutes (coolDownPeriod).
# Its exact refusal text is not known yet, hence the loose match.
COOLDOWN_MINUTES = 16
# Myntra answers a request within the cooldown with "Internal server error while scheduling ..."
COOLDOWN = re.compile(r"cool|wait|minute|already|recent|frequent|too many|try again|later|"
                      r"internal server error|while schedul", re.I)
QUERY = {"storeId": 2297, "partnerType": "PPMP"}  # store MYNTRA; the PPMP report also holds SJIT / FBM lines
HEADERS = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest",
           "x-myntra-app-name": "partners", "Origin": PORTAL, "Referer": f"{PORTAL}/Reports/ops-reports"}
# Akamai bot-manager cookies live for minutes or hours and are renewed by the server;
# they say nothing about when the login runs out
NOT_LOGIN = re.compile(r"^(_abck|ak_bmsc|bm_\w+|_ga\w*|_gid|_gcl\w*)$")
# SPF claims (captured from the portal, Oct 2026)
SPF_API = "https://partnersapi.myntrainfo.com/api/spf"
SPF_BLOB = "https://myntrascmuistaticassets.blob.core.windows.net/spf-claims"
SPF_STATUSES = ("OPEN", "IN_REVIEW", "ACCEPT", "REJECT", "MANUAL", "INVOICE_CREATED", "INVOICE_INITIATED",
                "PAYMENT_INITIATED", "PAYMENT_COMPLETED", "AWAITING_SELLER_RESPONSE",
                "AWAITING_AGENT_RESPONSE", "CLOSED", "DISPUTED")
SPF_BULK_MAX = 500                  # "Upload Order IDs for SPF (Max 500)"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# job statuses that will never complete (INTERRUPTED seen on old jobs, Oct 2026)
FAILED = ("FAIL", "ERROR", "CANCEL", "REJECT", "INTERRUPT", "ABORT", "EXPIRE", "TIMEOUT")


class SessionExpired(RuntimeError):
    """The saved portal session is missing, invalid or logged out."""


@dataclass
class Session:
    cookies: list[dict]
    saved_at: datetime | None = None

    def expires(self) -> datetime | None:
        """When the login cookies run out (the earliest one), or None when unknown."""
        ends = [c["expires"] for c in self.cookies
                if isinstance(c.get("expires"), (int, float)) and c["expires"] > 0
                and c.get("httpOnly") and not NOT_LOGIN.match(c.get("name", ""))]
        return datetime.fromtimestamp(min(ends)) if ends else None


def _cookie_header(text: str) -> list[dict]:
    """Cookies from a Cookie header line, a "Copy as cURL" command, or the request headers
    copied from DevTools (a line "cookie" with the value on the next line)."""
    m = re.search(r"(?:-b|--cookie)\s+(['\"])(.*?)\1", text, re.S) or \
        re.search(r"(?im)^cookie[ \t]*\r?\n[ \t]*([^\r\n]+)", text) or \
        re.search(r"(?im)^[ \t]*cookie:\s*([^'\"\r\n]+)", text)
    raw = m.group(m.lastindex) if m else text
    out = []
    for part in raw.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            out.append({"name": name, "value": value, "domain": ".myntrainfo.com"})
    return out


def _local(iso) -> datetime | None:
    """An ISO time ("...Z" from a browser, or local) as a naive local datetime."""
    if not isinstance(iso, str) or not iso:
        return None
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return t.astimezone().replace(tzinfo=None) if t.tzinfo else t


def parse_session(text: str) -> Session:
    text = (text or "").strip()
    if not text:
        raise SessionExpired("no Myntra session saved (MYNTRA_SESSION_JSON)")
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("cookies"), list):
        cookies = [c for c in data["cookies"] if "myntrainfo" in (c.get("domain") or "") and c.get("name")]
        return Session(cookies, _local(data.get("savedAt")))
    if isinstance(data, dict) and isinstance(data.get("cookie"), str):
        return Session(_cookie_header(data["cookie"]))
    cookies = _cookie_header(text)
    if not cookies:
        raise SessionExpired("Myntra session JSON not understood: expected cookies")
    return Session(cookies)


def from_worker(env, account: str = "MAIN") -> str:
    """The account's session last saved on the Telegram worker (by scripts/myntra/myntra_session.py or a run)."""
    url, token = env("DASHBOARD_URL"), env("DASHBOARD_TOKEN")
    if not url or not token:
        return ""
    import requests
    try:
        r = requests.post(f"{url.rstrip('/')}/api/myntra-session/read", timeout=30, params={"account": account.lower()},
                          headers={"Authorization": f"Bearer {token}"})
    except requests.RequestException as exc:
        log.warning("Myntra session not read from the worker: %s", exc)
        return ""
    if r.status_code != 200:
        log.info("No Myntra session on the worker (HTTP %s)", r.status_code)
        return ""
    return r.text


def save_to_worker(env, cookies: list[dict], account: str = "MAIN") -> bool:
    """Store the account's renewed cookies on the worker after a run, quietly (no Telegram message)."""
    url, token = env("DASHBOARD_URL"), env("DASHBOARD_TOKEN")
    if not url or not token or not cookies:
        return False
    import requests
    body = json.dumps({"savedAt": datetime.now().isoformat(timespec="seconds"), "source": "bot", "cookies": cookies})
    try:
        r = requests.post(f"{url.rstrip('/')}/api/myntra-session", data=body, timeout=30,
                          params={"account": account.lower()},
                          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    except requests.RequestException as exc:
        log.warning("Renewed Myntra session not saved: %s", exc)
        return False
    if not r.ok:
        log.warning("Renewed Myntra session not saved: HTTP %s", r.status_code)
    return r.ok


def session_text(env, default_file: Path, account: str = "MAIN") -> str:
    """The account's session saved on the worker; for MAIN also MYNTRA_SESSION_JSON, else the
    content of MYNTRA_SESSION_FILE (default_file when unset)."""
    text = from_worker(env, account)
    if account != "MAIN":
        return text
    text = text or env("MYNTRA_SESSION_JSON")
    if not text:
        path = Path(env("MYNTRA_SESSION_FILE") or default_file)
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
    return text


def _ts(ms) -> date | None:
    return datetime.fromtimestamp(ms / 1000).date() if ms else None


def job_range(job: dict, report_id: int = REPORT_ID) -> tuple[date, date] | None:
    """(first, last day) of a usable job of that report for the store: not failed, and
    requested after its last day (so that day was complete). None for any other job."""
    q = job.get("queryParams") or {}
    status = str(job.get("status") or "").upper()
    if job.get("reportId") != report_id or any(f in status for f in FAILED):
        return None
    if str(q.get("storeId")) != str(QUERY["storeId"]) or q.get("partnerType") != QUERY["partnerType"]:
        return None
    try:
        first, last = date.fromisoformat(q["reportStartDate"]), date.fromisoformat(q["reportEndDate"])
    except (KeyError, ValueError):
        return None
    created = _ts(job.get("createdOn"))
    return (first, last) if created and created > last else None


def exact_job(jobs: list[dict], report_id: int, start: date, end: date) -> dict | None:
    """Newest usable job of that report for exactly start..end (done or still running)."""
    fit = [j for j in jobs if job_range(j, report_id) == (start, end)]
    return max(fit, key=lambda j: j.get("createdOn") or 0) if fit else None


def _days(first: date, last: date) -> set[date]:
    return {first + timedelta(days=i) for i in range((last - first).days + 1)}


def plan(jobs: list[dict], days: list[date]) -> tuple[list[dict], list[dict], list[date]]:
    """Which existing jobs give the wanted days: (completed jobs to download, newest first;
    jobs still running to wait for; days no job covers)."""
    jobs = sorted((j for j in jobs if job_range(j)), key=lambda j: j.get("createdOn") or 0, reverse=True)
    need = set(days)
    done, running = [], []
    for want_done in (True, False):
        for job in jobs:
            finished = str(job.get("status") or "").upper() == "COMPLETED" and bool(job.get("successFile"))
            if finished != want_done:
                continue
            got = need & _days(*job_range(job))
            if got:
                (done if finished else running).append(job)
                need -= got
    return done, running, sorted(need)


class MyntraPortal:
    def __init__(self, session: Session, timeout: int = 60):
        from curl_cffi import requests as cr
        self.http = cr.Session(impersonate="chrome")
        for c in session.cookies:
            self.http.cookies.set(c["name"], c["value"], domain=c.get("domain") or ".myntrainfo.com",
                                  path=c.get("path") or "/")
        self.timeout = timeout

    def _call(self, method: str, path: str, **kw) -> dict:
        url = path if path.startswith("http") else f"{API}{path}"
        r = self.http.request(method, url, headers=HEADERS, timeout=self.timeout, **kw)
        if r.status_code in (401, 403):
            raise SessionExpired(f"portal answered HTTP {r.status_code}: logged out")
        try:
            data = r.json()
        except ValueError:
            # the login page comes back as HTML when the session has run out
            raise SessionExpired(f"portal answered HTTP {r.status_code} with a page, not data: logged out") from None
        status = data.get("status") or {}
        if r.status_code >= 400 or status.get("statusType") not in (None, "SUCCESS"):
            msg = status.get("statusMessage") or r.text[:200]
            if re.search(r"auth|login|session|unauthori", str(msg), re.I):
                raise SessionExpired(f"portal: {msg}")
            raise RuntimeError(f"Myntra portal {path}: HTTP {r.status_code} {msg}")
        return data

    def cookies(self) -> list[dict]:
        """The cookies as they are now: the portal renews its login tokens in its answers."""
        out: dict[str, dict] = {}
        for c in self.http.cookies.jar:
            if "myntrainfo" not in (c.domain or ""):
                continue
            # a cookie the portal set again (host partnersapi...) replaces the pasted copy (.myntrainfo.com)
            if c.name in out and (c.domain or "").startswith("."):
                continue
            out[c.name] = {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path or "/",
                           "expires": c.expires or -1, "httpOnly": not NOT_LOGIN.match(c.name)}
        return list(out.values())

    def partner_id(self) -> int | None:
        """The seller account this login is for (the portal's boot data names its partner id)."""
        r = self.http.get(f"{API.rsplit('/', 1)[0]}/boot/partners", timeout=self.timeout,
                          headers={**HEADERS, "x-myntra-knuth": "yes"})
        if r.status_code != 200:
            return None
        m = re.search(r'"sellerId":\s*"?(\d+)', r.text) or re.search(r'"partnerId":\s*"?(\d+)', r.text)
        return int(m.group(1)) if m else None

    def check(self) -> None:
        """Raises SessionExpired when the saved session no longer works."""
        self._call("GET", "/report/jobs/getWaitingTime")

    def jobs(self, size: int = 50) -> list[dict]:
        return self._call("GET", "/report/jobs/search",
                          params={"fetchSize": size, "sortBy": "id", "sortOrder": "desc", "start": 0}).get("data") or []

    def schedule(self, start: date, end: date, report_id: int = REPORT_ID, cooldown_wait: int = COOLDOWN_MINUTES) -> dict:
        """Request a report job. The portal refuses the same report again for 15 minutes after
        a request (coolDownPeriod); such a refusal is waited out once, up to cooldown_wait minutes."""
        body = {"reportId": report_id, "queryParams": {**QUERY, "reportStartDate": f"{start}",
                                                       "reportEndDate": f"{end}"}}
        try:
            data = self._call("POST", "/report/jobs/schedule", json=body).get("data") or []
        except RuntimeError as exc:
            if isinstance(exc, SessionExpired) or not cooldown_wait or not COOLDOWN.search(str(exc)):
                raise
            log.warning("Report %s in cooldown (%s): waiting %d min", report_id, exc, cooldown_wait)
            time.sleep(cooldown_wait * 60)
            data = self._call("POST", "/report/jobs/schedule", json=body).get("data") or []
        if not data:
            raise RuntimeError("Myntra portal did not return the scheduled report job")
        log.info("Report %s job %s scheduled for %s..%s", report_id, data[0].get("id"), start, end)
        return data[0]

    def _wait(self, job: dict, deadline: float, poll_seconds: int) -> dict:
        while True:
            status = str(job.get("status") or "").upper()
            if status == "COMPLETED" and job.get("successFile"):
                return job
            if any(f in status for f in FAILED):
                raise RuntimeError(f"Myntra report job {job.get('id')} {status}: {job.get('statusMessage')}")
            if time.time() > deadline:
                raise RuntimeError(f"Myntra report job {job.get('id')} not ready in time ({status})")
            log.info("Myntra report job %s: %s, waiting", job.get("id"), status or "?")
            time.sleep(poll_seconds)
            job = next((j for j in self.jobs() if j.get("id") == job.get("id")), job)

    def download(self, job: dict) -> tuple[str, bytes]:
        url = job["successFile"]
        # the blob itself is private: the portal answers with a redirect to a signed link
        r = self.http.get(f"{API.rsplit('/', 1)[0]}/file-manager/getDownloadUrl", params={"url": url},
                          headers=HEADERS, timeout=300, allow_redirects=True)
        if r.status_code != 200 or not r.content:
            raise RuntimeError(f"SJIT report {job.get('id')} download failed: HTTP {r.status_code}")
        name = url.rsplit("/", 1)[-1].split("?")[0] or f"Seller_Orders_Report_{job.get('id')}.csv"
        log.info("Myntra report %s downloaded (%d bytes)", name, len(r.content))
        return name, r.content

    def ensure(self, report_id: int, start: date, end: date) -> dict:
        """A job of that report for exactly start..end: an existing one (done or running), else a
        new request (a cooldown refusal is waited out)."""
        job = exact_job(self.jobs(100), report_id, start, end)
        if job:
            log.info("Report %s for %s..%s already on the portal (job %s, %s)",
                     report_id, start, end, job.get("id"), job.get("status"))
            return job
        return self.schedule(start, end, report_id)

    def collect(self, job: dict, wait_minutes: int = 40, poll_seconds: int = 30) -> tuple[str, bytes]:
        """(file name, CSV bytes) of the job once it is done."""
        job = self._wait(job, time.time() + wait_minutes * 60, poll_seconds)
        return self.download(job)

    def collect_or_regenerate(self, job: dict, wait_minutes: int = 45) -> tuple[str, bytes]:
        """collect(); when an existing (often old) job will not download - failed, expired link -
        the same report is asked for again (cooldown waited out) and the new job collected."""
        try:
            return self.collect(job, wait_minutes)
        except SessionExpired:
            raise
        except Exception as exc:  # noqa: BLE001
            q = job.get("queryParams") or {}
            start, end = date.fromisoformat(q["reportStartDate"]), date.fromisoformat(q["reportEndDate"])
            log.warning("Report job %s not downloaded (%s): asking for it again", job.get("id"), exc)
            new = self.schedule(start, end, job.get("reportId") or REPORT_ID)
            return self.collect(new, wait_minutes)

    def request(self, days: list[date]) -> dict | None:
        """Make sure a report for `days` exists or is on its way: schedules one only for the
        days no completed or running job covers. The new job, or None when nothing was needed."""
        _, _, missing = plan(self.jobs(), days)
        return self.schedule(missing[0], missing[-1]) if missing else None

    def reports(self, days: list[date], extra_from: date | None = None, wait_minutes: int = 20,
                poll_seconds: int = 30) -> tuple[list[tuple[str, bytes]], set[date]]:
        """(report files newest first, days they cover) for `days`. Reports already on the
        portal are used first; a report that cannot be downloaded, or days no report covers,
        get a new report, which is waited for. Completed reports from extra_from on are read
        too (cancellations of older orders), but never requested for."""
        jobs = self.jobs()
        done, running, missing = plan(jobs, days)
        got: list[tuple[dict, tuple[str, bytes]]] = []
        for job in done:
            try:
                got.append((job, self.download(job)))
            except Exception as exc:  # noqa: BLE001 - asked for again below
                log.warning("Existing SJIT report not usable: %s", exc)
        covered = set().union(*(_days(*job_range(j)) for j, _ in got)) if got else set()
        waiting = set().union(*(_days(*job_range(j)) for j in running)) if running else set()
        missing = sorted(set(days) - covered - waiting)
        if missing:
            running.append(self.schedule(missing[0], missing[-1]))
        deadline = time.time() + wait_minutes * 60
        for job in running:
            job = self._wait(job, deadline, poll_seconds)
            got.append((job, self.download(job)))
            covered |= _days(*job_range(job))
        if extra_from and extra_from < days[0]:
            # the newest report per earlier day, only as many as needed
            used = {j.get("id") for j, _ in got}
            older = sorted(_days(extra_from, days[0] - timedelta(days=1)) - covered)
            for job in plan([j for j in jobs if j.get("id") not in used], older)[0]:
                try:
                    got.append((job, self.download(job)))
                except Exception as exc:  # noqa: BLE001 - only extra cancellations
                    log.warning("Older SJIT report skipped: %s", exc)
        got.sort(key=lambda g: g[0].get("createdOn") or 0, reverse=True)
        return [f for _, f in got], covered

    # ------------------------------------------------------------ SPF claims (Support > Seller Protection Fund)

    def spf_tickets(self, model: str = "PPMP", page_size: int = 500, tries: int = 3) -> list[dict]:
        """Every SPF ticket of the account, any status: ticketId, orderLineId, returnId,
        issueCategory, status, createdOn (ISO, UTC).

        MAIN has 17,000+ tickets: 500 a page (the portal's 50 meant 340+ calls, and one call stuck
        for a minute on 23 Oct 2026 lost the whole list; 2,000 a page is refused). A page that
        times out or comes back as an error page is asked again, up to `tries` times."""
        out: list[dict] = []
        page = 0
        while True:
            params = [("fetchAccio", "true"), ("modelType", model), ("page", page), ("pageSize", page_size),
                      *(("status", s) for s in SPF_STATUSES)]
            for attempt in range(1, tries + 1):
                try:
                    data = self._call("GET", f"{SPF_API}/v2/getTickets", params=params)
                    break
                except Exception as exc:  # noqa: BLE001 - timeouts and error pages come and go
                    if attempt == tries:
                        raise
                    log.warning("SPF tickets page %d failed (%s): trying again", page, str(exc)[:120])
                    time.sleep(15 * attempt)
            rows = data.get("data") or []
            out += rows
            total = (data.get("status") or {}).get("totalCount") or 0
            if not rows or len(out) >= total:
                return out
            page += 1

    def spf_bulk_claim(self, order_line_ids: list, category: str, description: str,
                       model: str = "PPMP", seller: int | None = None) -> int:
        """One bulk claim, as the portal's "Delayed / Return Not Received" upload does it: an xlsx
        with one OrderId column (order line ids, max SPF_BULK_MAX) is put on the claims blob store,
        then createRnrBulkTickets is called with its link. Returns the job id; the tickets turn up
        in spf_tickets() a minute or so later."""
        import io
        from openpyxl import Workbook
        if not 0 < len(order_line_ids) <= SPF_BULK_MAX:
            raise ValueError(f"a bulk claim takes 1 to {SPF_BULK_MAX} order lines, not {len(order_line_ids)}")
        wb = Workbook()
        ws = wb.active
        ws.append(["OrderId"])
        for oli in order_line_ids:
            ws.append([int(oli)])
        buf = io.BytesIO()
        wb.save(buf)
        seller = seller or self.partner_id() or 0
        name = f"{seller}_{int(time.time() * 1000)}"
        token = self._call("GET", f"{API.rsplit('/', 1)[0]}/file-manager/getUploadToken/spfJob",
                           params={"fileNames": name}).get(name) or {}
        if not token.get("sasUrl") or not token.get("fileNameWithPath"):
            raise RuntimeError("Myntra SPF upload token missing from the portal's answer")
        r = self.http.put(token["sasUrl"], data=buf.getvalue(), timeout=self.timeout,
                          headers={"x-ms-blob-type": "BlockBlob", "Content-Type": XLSX})
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Myntra SPF claim file upload failed: HTTP {r.status_code}")
        data = self._call("POST", f"{SPF_API}/createRnrBulkTickets", json={
            "fileUrl": f"{SPF_BLOB}/{token['fileNameWithPath']}", "description": description,
            "issueCategory": category, "modelType": model})
        if not data.get("jobId"):
            raise RuntimeError(f"Myntra SPF bulk claim not accepted: {str(data)[:200]}")
        log.info("SPF bulk claim %s: %d order lines, job %s", category, len(order_line_ids), data["jobId"])
        return int(data["jobId"])


def expiry_warning(session: Session, warn_days: int, now: datetime | None = None, life_days: int = 15) -> str:
    """A line for Telegram when the login runs out within warn_days, else ''. Without cookie
    expiry dates (a pasted Cookie header) the session is taken to last life_days from saving."""
    now = now or datetime.now()
    ends = session.expires()
    guessed = not ends and session.saved_at is not None
    if guessed:
        ends = session.saved_at + timedelta(days=life_days)
    if not ends:
        return ""
    left = ends - now
    if left > timedelta(days=warn_days):
        return ""
    days = max(0, left.days)
    when = "today" if days == 0 else f"in {days} day(s)"
    if guessed:
        return f"Myntra portal session saved {session.saved_at:%d %b}: may expire soon ({when}, about {life_days} days)"
    return f"Myntra portal session expires {ends:%d %b %H:%M} ({when})"
