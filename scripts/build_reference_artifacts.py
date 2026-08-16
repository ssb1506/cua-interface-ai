"""
Builds the two reference capability artifacts directly from the schema,
matching a verified real trace against the live target app (accessibility
tree captured via agent/perception.py against the running app - see
evidence/manual_trace/ for the raw captures this was built from).

This exists so the replay engine, safety gate, and error-taxonomy can be
demonstrated end-to-end for real (they need no LLM) even before a
discovery run has been executed with a live model API key. It is NOT a
substitute for the required LLM-driven discovery run - see README.md.
"""
from pathlib import Path

from agent.schema import (
    Capability, Step, Locator, LocatorKind, ActionType, RiskLevel, Checkpoint, Outcome,
    InputParam, OutputField,
)

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"
ARTIFACTS_DIR.mkdir(exist_ok=True)


def build_lookup_savings_balance() -> Capability:
    return Capability(
        capability_id="lookup_savings_balance",
        description="Look up a member by ID and read their current savings balance.",
        target={"allowed_domains": ["localhost:5055", "127.0.0.1:5055"], "app": "coreserv-legacy"},
        input_params=[InputParam(name="member_id", type="string", description="Member ID to search for")],
        outputs=[OutputField(name="savings_balance", type="string", description="Current savings balance, as displayed")],
        steps=[
            Step(step_id="s0", action=ActionType.NAVIGATE, risk=RiskLevel.SAFE, literal_value="http://localhost:5055/"),
            Step(
                step_id="s1", action=ActionType.FILL, risk=RiskLevel.SAFE,
                locator=[Locator(kind=LocatorKind.ROLE_NAME, role="textbox", value="")],
                value_ref="member_id",
                notes="Home page has a single unlabeled textbox - role alone disambiguates it (legacy app, no <label>).",
            ),
            Step(
                step_id="s2", action=ActionType.CLICK, risk=RiskLevel.SAFE,
                locator=[Locator(kind=LocatorKind.ROLE_NAME, role="button", value="Search")],
                checkpoint=Checkpoint(description="navigates to member detail or shows not-found", expect="visible"),
            ),
            Step(
                step_id="s3", action=ActionType.EXTRACT, risk=RiskLevel.SAFE,
                locator=[
                    Locator(kind=LocatorKind.ROLE_NAME, role="text", value="$", brittle=False),
                    Locator(kind=LocatorKind.CSS, value="#balance-cell", brittle=True),
                ],
                extract_as="savings_balance",
                notes="Primary locator matches the balance text node; CSS id is a brittle fallback only.",
            ),
        ],
        outcomes=[
            Outcome(
                name="success", kind="success", description="Balance read successfully.",
                detection=Checkpoint(description="balance cell visible", locator=Locator(kind=LocatorKind.CSS, value="#balance-cell"), expect="visible"),
            ),
            Outcome(
                name="member_not_found", kind="business_outcome",
                description="No member exists with the given ID - a legitimate result, not a failure.",
                detection=Checkpoint(description="'No record found' text visible", expect="text_contains", expect_value="No record found"),
            ),
        ],
        approval_state="approved",
        provenance={"built_from": "verified manual trace against live app; see evidence/manual_trace/"},
    )


def build_open_subaccount() -> Capability:
    return Capability(
        capability_id="open_subaccount",
        description="Open a new sub-account for a member and reach the confirmation screen.",
        target={"allowed_domains": ["localhost:5055", "127.0.0.1:5055"], "app": "coreserv-legacy"},
        input_params=[
            InputParam(name="member_id", type="string"),
            InputParam(name="account_type", type="string", description="CHECKING or MONEY_MARKET"),
            InputParam(name="initial_deposit", type="string"),
        ],
        outputs=[OutputField(name="confirmation_text", type="string", description="Confirmation banner text")],
        steps=[
            Step(step_id="s0", action=ActionType.NAVIGATE, risk=RiskLevel.SAFE,
                 literal_value="http://localhost:5055/subaccount/new?member_id={member_id}",
                 value_ref="member_id",
                 notes="URL is templated with member_id at replay time."),
            Step(
                step_id="s1", action=ActionType.SELECT, risk=RiskLevel.SAFE,
                locator=[Locator(kind=LocatorKind.ROLE_NAME, role="combobox", value="")],
                value_ref="account_type",
            ),
            Step(
                step_id="s2", action=ActionType.FILL, risk=RiskLevel.SAFE,
                locator=[Locator(kind=LocatorKind.ROLE_NAME, role="textbox", value="")],
                value_ref="initial_deposit",
            ),
            Step(
                step_id="s3", action=ActionType.CLICK, risk=RiskLevel.RISKY,
                locator=[Locator(kind=LocatorKind.ROLE_NAME, role="button", value="Continue")],
                checkpoint=Checkpoint(description="confirmation or validation-error page loads", expect="visible"),
                notes="State-mutating (creates a real sub-account) - classified RISKY, gated by SafetyGate.check_risk.",
            ),
            Step(
                step_id="s4", action=ActionType.EXTRACT, risk=RiskLevel.SAFE,
                locator=[Locator(kind=LocatorKind.TEXT, value="opened for member")],
                extract_as="confirmation_text",
            ),
        ],
        outcomes=[
            Outcome(name="success", kind="success", description="Sub-account created and confirmed.",
                    detection=Checkpoint(description="Confirmation heading visible", expect="text_contains", expect_value="Confirmation")),
            Outcome(name="validation_error", kind="business_outcome", description="Form submitted with invalid input.",
                    detection=Checkpoint(description="validation error banner visible", expect="text_contains", expect_value="Validation error")),
        ],
        approval_state="approved",
        provenance={"built_from": "verified manual trace against live app; see evidence/manual_trace/"},
    )


if __name__ == "__main__":
    for cap in (build_lookup_savings_balance(), build_open_subaccount()):
        path = ARTIFACTS_DIR / f"{cap.capability_id}.json"
        path.write_text(cap.model_dump_json(indent=2))
        print(f"wrote {path}")
