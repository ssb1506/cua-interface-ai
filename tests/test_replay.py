"""
Integration tests against the live mock app. Requires:
    python app/server.py    (running on :5055)
"""
from pathlib import Path

import pytest

from agent.replay import replay

LOOKUP = Path("artifacts/lookup_savings_balance.json")
SUBACCOUNT = Path("artifacts/open_subaccount.json")


def test_replay_success_reads_balance():
    result = replay(LOOKUP, {"member_id": "12345"})
    assert result.status == "success"
    assert result.outputs["savings_balance"] == "$4,213.55"


def test_replay_business_outcome_not_found():
    result = replay(LOOKUP, {"member_id": "00000"})
    assert result.status == "business_outcome"
    assert result.business_outcome == "not_found"


def test_replay_blocks_risky_action_without_flag():
    result = replay(SUBACCOUNT, {"member_id": "67890", "account_type": "CHECKING", "initial_deposit": "10"}, allow_risky=False)
    assert result.status == "failure"
    assert result.error["kind"] == "guardrail_block"


def test_replay_risky_action_with_flag_succeeds():
    result = replay(SUBACCOUNT, {"member_id": "67890", "account_type": "CHECKING", "initial_deposit": "10"}, allow_risky=True)
    assert result.status == "success"
    assert "opened for member" in result.outputs["confirmation_text"]


def test_replay_validation_error_business_outcome():
    result = replay(SUBACCOUNT, {"member_id": "67890", "account_type": "", "initial_deposit": "10"}, allow_risky=True)
    assert result.status == "business_outcome"
    assert result.business_outcome == "validation_error"
