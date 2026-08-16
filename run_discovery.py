"""
Demo entry point for a discovery run.

Example (member lookup capability):
    export ANTHROPIC_API_KEY=sk-...
    python app/server.py &                     # start the target app on :5055
    python run_discovery.py lookup_savings_balance

Example (open sub-account capability):
    python run_discovery.py open_subaccount
"""
import sys

from agent.discovery import run_discovery
from agent.schema import InputParam, OutputField

GOALS = {
    "lookup_savings_balance": dict(
        goal="Look up member 12345 and read their current savings balance.",
        start_url="http://localhost:5055/",
        capability_id="lookup_savings_balance",
        input_params=[InputParam(name="member_id", type="string", description="Member ID to look up")],
        output_fields=[OutputField(name="savings_balance", type="string", description="Current savings balance")],
    ),
    "open_subaccount": dict(
        goal="Open a new Checking sub-account for member 67890 with a $50 initial deposit and reach the confirmation screen.",
        start_url="http://localhost:5055/",
        capability_id="open_subaccount",
        input_params=[
            InputParam(name="member_id", type="string"),
            InputParam(name="account_type", type="string"),
            InputParam(name="initial_deposit", type="string"),
        ],
        output_fields=[OutputField(name="confirmation_text", type="string")],
    ),
}

if __name__ == "__main__":
    key = sys.argv[1] if len(sys.argv) > 1 else "lookup_savings_balance"
    cfg = GOALS[key]
    path = run_discovery(**cfg)
    print(f"Artifact saved: {path}")
