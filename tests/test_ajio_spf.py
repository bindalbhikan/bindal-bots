from datetime import date

import pytest

from bindal.ajio_spf import claims
from bindal.ajio_spf.pipeline import dry_run
from bindal.portals.ajio import AjioClient, AjioError


@pytest.mark.parametrize("cli,env_value,expected", [
    (None, "", True), (None, "1", True), (None, "true", True), (None, "yes", True),
    (None, "garbage", True), (None, "0", False), (None, "false", False), (None, "off", False),
    ("1", "0", True), ("0", "1", False),
])
def test_dry_run_is_the_safe_default(monkeypatch, cli, env_value, expected):
    monkeypatch.setenv("SPF_DRY_RUN", env_value)
    assert dry_run(cli) is expected


def test_row_facts_keep_both_courier_statuses():
    facts = claims.row_facts({"3PL Delivery Status": "RETURN DELIVERED", "COURIER STATUS": "Picked up"})
    assert facts["ajio_3pl_status"] == "RETURN DELIVERED"
    assert facts["courier_status"] == "Picked up"


def test_dispute_after_five_days_not_when_received():
    rules = claims.Rules()
    row = {"RETURN ORDER NUMBER": "RT1", "Return Delivered Date": date(2026, 10, 1)}
    assert claims.decide(row, date(2026, 10, 7), rules)[0] == claims.DISPUTE
    assert claims.decide({**row, "OMSGURU STATUS": "Return Received"}, date(2026, 10, 7), rules) is None
    assert claims.decide({**row, "TICKET ID": "123"}, date(2026, 10, 7), rules) is None
    assert claims.decide({**row, "COURIER PICKUP": "NOT PICKED UP"}, date(2026, 10, 7), rules) is None


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


def client_answering(status, body=None):
    c = AjioClient("u", "p")
    c._req = lambda *a, **k: FakeResponse(status, body)
    return c


def test_ticket_check_failure_never_reads_as_no_ticket():
    with pytest.raises(AjioError):
        client_answering(401, {"error": "expired"}).tickets_for_return("RT1")
    with pytest.raises(AjioError):
        client_answering(500).tickets_for_return("RT1")


def test_ticket_check_answers():
    assert client_answering(404).tickets_for_return("RT1") == []
    assert client_answering(200, {"result": [{"ticketId": 7}]}).tickets_for_return("RT1") == [{"ticketId": 7}]
    assert client_answering(200, {"result": {"ticketId": 8}}).tickets_for_return("RT1") == [{"ticketId": 8}]
