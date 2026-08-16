"""
Demo entry point for deterministic replay - no LLM in the loop.

Example:
    python run_replay.py artifacts/lookup_savings_balance.json member_id=12345
    python run_replay.py artifacts/lookup_savings_balance.json member_id=99999   # -> business_outcome: not_found
    python run_replay.py artifacts/open_subaccount.json member_id=67890 account_type=CHECKING initial_deposit=50 --allow-risky
"""
import json
import sys
from pathlib import Path

from agent.replay import replay

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    artifact_path = Path(sys.argv[1])
    allow_risky = "--allow-risky" in sys.argv
    kv_args = [a for a in sys.argv[2:] if "=" in a]
    params = dict(a.split("=", 1) for a in kv_args)

    result = replay(artifact_path, params, allow_risky=allow_risky)
    print(json.dumps(result.to_dict(), indent=2))
