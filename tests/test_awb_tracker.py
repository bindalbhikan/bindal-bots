import pytest

from bindal.awb_tracker import CourierRegistry, Job, TrackResult, track_all
from bindal.awb_tracker import models as m


@pytest.mark.parametrize("text,bucket", [
    ("PickDone", m.PICKED_UP),                 # XpressBees milestone code
    ("IN_TRANSIT", m.IN_TRANSIT),              # Delhivery status code
    ("OUT_FOR_DELIVERY", m.OUT_FOR_DELIVERY),
    ("ShipmentRtoConfirmed", m.RTO),
    ("Data Received", m.PENDING),
    ("Pickup Cancelled", m.CANCELLED),
    ("Shipment picked up", m.PICKED_UP),
    ("DLVD", m.DELIVERED),                     # XpressBees milestone code
    ("Shipment Detained By Regulatory Authorities", m.IN_TRANSIT),
    ("will be delivered by Saturday", m.UNKNOWN),
])
def test_classify(text, bucket):
    assert m.classify(text) == bucket


def test_wrong_courier_name_falls_back_to_awb_shape(monkeypatch):
    reg = CourierRegistry({})
    assert reg.alternatives("4884668263645", "DELHIVERY") == ["XPRESSBEES"]
    assert reg.alternatives("82130131580", "BLUEDART") == []

    def fake_fetch(self, awb):
        if self.name == "DELHIVERY":
            return TrackResult(awb=awb, partner="DELHIVERY", status=m.NOT_FOUND, error="not found")
        return TrackResult(awb=awb, partner=self.name, ok=True, status=m.IN_TRANSIT)

    from bindal.awb_tracker.base import BaseCourier
    monkeypatch.setattr(BaseCourier, "fetch", fake_fetch)
    [res] = track_all([Job("4884668263645", "Delhivery")], reg, workers=1, progress=False)
    assert res.ok and res.partner == "XPRESSBEES"


def test_meaningless_latest_status_uses_scan_history():
    r = m.TrackResult(awb="1", partner="BLUEDART", ok=True, status_raw="Contact Customer Service",
                      events=[m.TrackEvent("2026-10-01T10:00:00", "Shipment Picked Up"),
                              m.TrackEvent("2026-10-02T10:00:00", "Contact Customer Service")])
    assert r.finalize().status == m.PICKED_UP
