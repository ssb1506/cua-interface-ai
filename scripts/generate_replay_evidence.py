"""
Generates clearly-labeled replay evidence for /evidence/ by running the
real replay engine against the live app for each scenario and copying the
run's evidence folder to a descriptive name. Requires app/server.py to be
running on :5055.
"""
import json
import shutil
from pathlib import Path

from agent.replay import replay, EVIDENCE_DIR

LOOKUP = Path("artifacts/lookup_savings_balance.json")
SUBACCOUNT = Path("artifacts/open_subaccount.json")

SCENARIOS = [
    ("replay_success_lookup_balance", LOOKUP, {"member_id": "12345"}, False),
    ("replay_business_outcome_member_not_found", LOOKUP, {"member_id": "00000"}, False),
    ("replay_guardrail_block_risky_unconfirmed", SUBACCOUNT,
     {"member_id": "67890", "account_type": "CHECKING", "initial_deposit": "10"}, False),
    ("replay_success_open_subaccount", SUBACCOUNT,
     {"member_id": "67890", "account_type": "MONEY_MARKET", "initial_deposit": "100"}, True),
    ("replay_business_outcome_validation_error", SUBACCOUNT,
     {"member_id": "67890", "account_type": "", "initial_deposit": "10"}, True),
]

if __name__ == "__main__":
    summary = {}
    for label, artifact, params, allow_risky in SCENARIOS:
        result = replay(artifact, params, allow_risky=allow_risky)
        src = EVIDENCE_DIR / result.run_id
        dst = EVIDENCE_DIR / label
        if dst.exists():
            shutil.rmtree(dst)
        shutil.move(str(src), str(dst))
        (dst / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
        (dst / "params.json").write_text(json.dumps({"artifact": str(artifact), "params": params, "allow_risky": allow_risky}, indent=2))
        summary[label] = result.status
        print(f"{label}: {result.status}")
    print(json.dumps(summary, indent=2))
