"""Follow-up on open SPF tickets until the return arrives or AJIO pays.

A ticket needs us when AJIO spoke last (a reply, or a close / resolve without
our answer) and the return is still not in the warehouse. The reply is drafted
by Gemini from the whole conversation plus our own evidence; if Gemini is
unavailable, the fixed rules from ajio-ticket-followup.js are used. Posting a
comment on a closed ticket re-opens it.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests

from .inputs import is_seller, plain, sorted_comments

log = logging.getLogger(__name__)

CLOSED = {"RESOLVED", "CLOSED", "COMPLETED", "CANCELLED", "CANCELED", "REJECTED", "AUTO_CLOSED", "WITHDRAWN"}
# AJIO's automatic acknowledgement; answering it at once only looks like spam.
GENERIC_ACK = re.compile(r"working on (your|the) request|will update (it|you)? ?shortly", re.I)
GENERIC_WAIT_HOURS = 72

# Small models first: their free-tier daily quota is far larger; the bigger
# flash models are only the fallback.
GEMINI_MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3-flash-preview",
                 "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Fallback when Gemini is down - the rules from ajio-ticket-followup.js.
FALLBACK_RULES = [
    ([r"working on (your|the) request|will update (it|you)? ?shortly"],
     "Any update on this?\n\nWe have still not received the return yet. Kindly check and update us on the status."),
    ([r"marked as lost", r"claim (is|has been) approved|reimbursement policy"],
     "Please share the UTR if the payment has been processed.\n\n"
     "If the payment has not been processed yet, kindly share the settlement date."),
    ([r"return has been delivered|receipt is (now )?closed"],
     "We have not received the return yet. Kindly check again.\n\n"
     "If the return has been delivered, please share the POD. Otherwise, please reimburse the invoice amount."),
]

PROMPT = """You handle AJIO Seller Protection Fund (SPF) claim tickets for BINDAL EXPORTS LIMITED, a dropship seller on AJIO.
Your only goal is to get the claim settled: either the return parcel physically reaches our warehouse, or AJIO reimburses the invoice amount.

Read the ticket conversation and our evidence, then decide whether to reply now, and if so write the reply.

How to answer what AJIO said:
- AJIO says the return was delivered / handed over / receipt closed: we have NO inward entry for it (no OMSGuru receipt, no ERP GRN, not in the warehouse register). Ask for the POD with receiver name, signature, date and photo of the parcel at our warehouse; without a valid POD ask them to reimburse the invoice amount under SPF.
- AJIO says "working on it" / "will update shortly" / generic acknowledgement with no outcome: politely push for a concrete resolution and a timeline; mention how long the claim has been open.
- AJIO says the claim is approved / shipment marked lost / reimbursement will be processed: ask for the UTR number and settlement date. If they already gave a UTR or say payment is done, do not reply (action "skip").
- AJIO closed or resolved the ticket without settling it, or with a reason that does not match our facts: re-open by replying, state point by point why it is not resolved, and ask for reimbursement.
- AJIO asks for documents or information we cannot see in the evidence (CCTV, unboxing video, images): reply that the parcel never reached our warehouse so there is nothing to unbox, offer the forward dispatch details we have, and ask them to verify with the courier and reimburse.
- AJIO says the claim is rejected: contest it with our facts and ask for escalation to the SPF team.
- If AJIO's latest message needs nothing from us, or the issue is clearly settled, use action "skip".

Rules for the reply:
- English, polite but firm, 40 to 120 words, plain text, no markdown, no greeting line like "Dear Team" more than once, sign off as "Team Bindal Exports".
- Quote only facts from the evidence block (return order, AWB, dates, invoice, amount). Never invent dates, UTRs, POD details or promises.
- Do not repeat our previous reply word for word; move the conversation forward.

Answer with JSON only: {{"action": "reply" or "skip", "reason": "<one short line>", "reply": "<text, empty when skip>"}}

EVIDENCE (our records):
{facts}

TICKET:
Ticket ID {ticket_id} | status {status} | type {sub_issue} | created {created}

CONVERSATION (oldest first):
{conversation}
"""


@dataclass
class Plan:
    ticket_id: str
    status: str
    reopen: bool
    reply: str
    reason: str
    source: str          # gemini model name or "rule"
    return_order: str


def ts(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).strip().replace(" ", "T")[:19])
    except ValueError:
        return None


def needs_us(ticket: dict, seller_id: str, now: datetime, cooldown_hours: int) -> bool:
    """AJIO has the last word (a comment, or a close we have not answered) and we are past the cooldown."""
    cs = sorted_comments(ticket)
    ours = [c for c in cs if is_seller(c, seller_id)]
    last_ours = ts(ours[-1].get("createdAt")) if ours else None
    if last_ours and now - last_ours < timedelta(hours=cooldown_hours):
        return False
    last = cs[-1] if cs else None
    if last is not None and not is_seller(last, seller_id):
        said_at = ts(last.get("createdAt"))
        if GENERIC_ACK.search(plain(last.get("content"))) and said_at and now - said_at < timedelta(hours=GENERIC_WAIT_HOURS):
            return False
        return True
    status = str(ticket.get("status") or "").upper()
    if status in CLOSED:
        closed_at = ts(ticket.get("resolvedDate") or ticket.get("lastUpdatedOn"))
        return last_ours is None or (closed_at is not None and closed_at > last_ours)
    return False


def conversation_text(ticket: dict, seller_id: str, limit: int = 14) -> str:
    lines = []
    for c in sorted_comments(ticket)[-limit:]:
        who = "US (seller)" if is_seller(c, seller_id) else f"AJIO ({c.get('authorName') or c.get('authorType') or 'support'})"
        lines.append(f"[{str(c.get('createdAt') or '')[:16]}] {who}: {plain(c.get('content'))[:1200]}")
    if ticket.get("resolution"):
        lines.append(f"[resolution] AJIO: {plain(ticket.get('resolution'))[:800]}")
    return "\n".join(lines) or "(no comments yet)"


class Gemini:
    def __init__(self, api_key: str, models=GEMINI_MODELS):
        self.key, self.models = api_key, models
        self.down_until: dict[str, float] = {}

    def decide(self, prompt: str) -> tuple[dict, str] | None:
        """(answer, model) from the first model that answers; every failure is logged.

        503 (busy) is retried on the same model after a short wait; 429 (free-tier
        per-minute limit) waits the delay Google asks for, once, then moves on.
        """
        for model in self.models:
            if self.down_until.get(model, 0) > time.time():
                continue
            for attempt in range(3):
                try:
                    r = requests.post(GEMINI_URL.format(model=model), timeout=120,
                                      headers={"x-goog-api-key": self.key},
                                      json={"contents": [{"parts": [{"text": prompt}]}],
                                            "generationConfig": {"responseMimeType": "application/json",
                                                                 "temperature": 0.4}})
                except requests.RequestException as exc:
                    log.warning("Gemini %s: %s", model, exc)
                    time.sleep(5)
                    continue
                if r.status_code == 503:
                    log.warning("Gemini %s busy (503), attempt %d", model, attempt + 1)
                    time.sleep(10 * (attempt + 1))
                    continue
                if r.status_code == 429:
                    wait = _retry_delay(r) or 30
                    log.warning("Gemini %s rate limited (429), waiting %ss: %s", model, wait, _message(r))
                    if attempt == 0 and wait <= 70:
                        time.sleep(wait)
                        continue
                    self.down_until[model] = time.time() + 900
                    break
                if not r.ok:
                    log.warning("Gemini %s: HTTP %s %s", model, r.status_code, _message(r))
                    self.down_until[model] = time.time() + 3600
                    break
                try:
                    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                    out = json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip()))
                    if out.get("action") in ("reply", "skip"):
                        return out, model
                    log.warning("Gemini %s answered without an action: %s", model, text[:200])
                except (KeyError, IndexError, ValueError) as exc:
                    log.warning("Gemini %s gave an unreadable answer: %s", model, exc)
                break
            else:
                self.down_until[model] = time.time() + 120
        log.warning("No Gemini model answered - using the fixed reply rules")
        return None


def _message(r) -> str:
    try:
        return str(r.json().get("error", {}).get("message", ""))[:200]
    except ValueError:
        return r.text[:200]


def _retry_delay(r) -> int | None:
    try:
        for d in r.json().get("error", {}).get("details", []):
            if "retryDelay" in d:
                return int(float(str(d["retryDelay"]).rstrip("s"))) + 1
    except (ValueError, AttributeError):
        pass
    return None


def fallback(ticket: dict, seller_id: str) -> str | None:
    cs = [c for c in sorted_comments(ticket) if not is_seller(c, seller_id)]
    said = plain(cs[-1].get("content")).lower() if cs else plain(ticket.get("resolution")).lower()
    for patterns, reply in FALLBACK_RULES:
        if said and all(re.search(p, said) for p in patterns):
            return reply
    if str(ticket.get("status") or "").upper() in CLOSED:
        return ("This ticket has been closed but the issue is not resolved. The return has still not been "
                "received at our warehouse. Kindly re-open, share the POD if it was delivered, or reimburse "
                "the invoice amount under SPF.\n\nTeam Bindal Exports")
    return None


def plan_reply(ticket: dict, facts: dict, seller_id: str, gemini: Gemini | None) -> Plan | None:
    status = str(ticket.get("status") or "").upper()
    tid = str(ticket.get("ticketId"))
    rt = (ticket.get("orderDetails") or {}).get("returnOrderId") or facts.get("return_order", "")
    if gemini is not None:
        prompt = PROMPT.format(
            facts=json.dumps(facts, ensure_ascii=False, indent=1), ticket_id=tid, status=status,
            sub_issue=ticket.get("subIssueType") or ticket.get("subCategory") or "",
            created=str(ticket.get("createdDate") or "")[:10], conversation=conversation_text(ticket, seller_id))
        got = gemini.decide(prompt)
        if got is not None:
            out, model = got
            if out["action"] == "skip" or not str(out.get("reply") or "").strip():
                log.info("Ticket %s: Gemini skips (%s)", tid, out.get("reason"))
                return None
            return Plan(tid, status, status in CLOSED, str(out["reply"]).strip(), str(out.get("reason") or ""), model, rt)
    reply = fallback(ticket, seller_id)
    if reply:
        return Plan(tid, status, status in CLOSED, reply, "fixed rule (Gemini unavailable)", "rule", rt)
    return None
