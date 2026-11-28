from app.agent.sync import decide


def test_decide_forward_only_and_terminal():
    assert decide("applied", "technical", "upcoming", "")[0] == "interview"
    assert decide("saved", "oa", "upcoming", "")[0] == "oa"
    assert decide("oa", "hr", "upcoming", "")[0] == "interview"
    assert decide("interview", "oa", "upcoming", "") is None      # never moves backward
    assert decide("interview", "hr", "upcoming", "") is None      # already there
    for terminal in ("offer", "rejected", "withdrawn"):
        assert decide(terminal, "hr", "upcoming", "") is None
    assert decide("applied", "hr", "cancelled", "") is None
    assert decide("interview", "hr", "done", "Rejected")[0] == "rejected"
    assert decide("interview", "hr", "done", "Offer")[0] == "offer"
    assert decide("interview", "hr", "done", "Went badly") is None
    assert decide("interview", "hr", "done", "Waiting to hear back") is None
