from bindal.myntra_spf.pipeline import final_status


def test_final_status_matches_the_master_formula():
    assert final_status({"SPF PAID": 120}) == "SPF Paid"
    assert final_status({"OMSGURU STATUS": "Return Received ERP"}) == "Return Received"
    assert final_status({"MANUAL RETURN STATUS": "Pickup Not Done"}) == "Return Received"
    assert final_status({"ORDER SETTLEMENT": 450.5}) == "Payment Received"
    assert final_status({"ORDER SETTLEMENT": 450.5, "RETURN DEBIT": -450.5}) == ""
    assert final_status({"OMSGURU STATUS": "Return Init"}) == ""      # blank: looked up in OMSGuru
