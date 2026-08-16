# Computer-Use Automation System

A small, real vertical slice of: goal → LLM-driven discovery against a live UI → saved capability artifact → deterministic replay with typed params/outputs and error handling → human escalation that takes over the live session. See `/REPORT.md` for design rationale and trade-offs, and `/evidence/README.md` for what's been actually run vs. what's pending.

## Setup

The only "live service" this project depends on is the mock target app (`app/server.py`) started below — it's a local Flask app with no external calls or hidden dependencies. Everything except the discovery step (replay, safety tests, escalation demo) runs fully offline against it, no internet or API access needed.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

For the discovery run only, you need model API access. Default provider is **Gemini** (free tier, no payment method required):
```bash
# get a free key at https://aistudio.google.com -> "Get API key" -> "Create API key"
export GEMINI_API_KEY=...
# optional: export CUA_MODEL=gemini-2.5-flash
```
To use Anthropic instead (if you have Claude API credits):
```bash
export CUA_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-...
```
Replay, safety tests, and the escalation demo need **no** API key - they don't invoke an LLM.

## Target application

`app/server.py` is a small mock "legacy" bank back-office app (nested tables, no test IDs, no client framework) standing in for the real environment: member search → member detail with balance → open sub-account form → confirmation, plus toggleable runtime failure modes (`?simulate_perm_denied=1`, `?simulate_session_expired=1`) and organic ones (unknown member ID, invalid form input) used to exercise the error taxonomy in Section 3.3 of the brief.

Start it first, in its own terminal:
```bash
python app/server.py
# serves http://localhost:5055
```

## Demo path

### 1. Deterministic replay (works right now, no API key needed)

Two reference artifacts are already checked in under `/artifacts/` (built from a verified manual trace against the live app - see `scripts/build_reference_artifacts.py` and `evidence/README.md` for why).

```bash
# success
python run_replay.py artifacts/lookup_savings_balance.json member_id=12345

# business outcome, not a crash
python run_replay.py artifacts/lookup_savings_balance.json member_id=99999

# risky action blocked without confirmation
python run_replay.py artifacts/open_subaccount.json member_id=67890 account_type=CHECKING initial_deposit=50

# risky action, explicitly confirmed
python run_replay.py artifacts/open_subaccount.json member_id=67890 account_type=CHECKING initial_deposit=50 --allow-risky
```

### 2. LLM-driven discovery (requires a free `GEMINI_API_KEY`, or `ANTHROPIC_API_KEY` with `CUA_PROVIDER=anthropic`)

```bash
python run_discovery.py lookup_savings_balance
# or
python run_discovery.py open_subaccount
```
Produces a fresh `artifacts/<capability_id>.json` and a full evidence trail (per-step observation, model decision, screenshot) in `evidence/discovery-<run_id>/`. You can then replay that artifact exactly as in step 1.

### 3. Human escalation / handoff (no API key needed)

```bash
python scripts/test_escalation.py
# it will pause and print an evidence run dir; in a second terminal:
python operator_console.py evidence/<printed-run-dir>
#   operator> fill textbox "" 12345
#   operator> click button Search
#   operator> resume
```
This proves control transfer against the *same live session* (same CDP-attached browser/page), not a fresh one - see `evidence/README.md` for the verified before/after state.

### 4. Tests

```bash
python app/server.py &      # tests replay against the live app
PYTHONPATH=. python -m pytest tests/ -v
```
14 tests: safety guardrails, artifact schema validation, and live replay integration (success / business outcome / guardrail block, run for real against `app/server.py`).

## Repository layout

```
app/server.py              mock legacy target application
agent/schema.py            the Capability artifact contract
agent/perception.py        accessibility-tree observation layer
agent/llm_client.py        Gemini client (default, free tier) with an Anthropic fallback; forced tool/function-call action schema
agent/discovery.py         LLM-driven observe-decide-act loop -> artifact
agent/replay.py            deterministic replay engine, no LLM
agent/safety.py            allowlist, risk classification, redaction
agent/escalation.py        pause / intervention-request / wait-for-resume
operator_console.py        mocked human operator CLI (attaches over CDP)
artifacts/                 saved capability artifacts
evidence/                  logs, screenshots, results from real runs
scripts/                   helper scripts (reference artifacts, evidence gen, escalation test)
tests/                     pytest suite
REPORT.md                  design write-up
```

## What's real vs. mocked

- **Real:** the target app, the artifact schema, deterministic replay (including its full error taxonomy against a live app), safety guardrails, the escalation control-transfer mechanism (verified two-process handoff over CDP), and the LLM-driven discovery loop itself - a real Gemini 2.5 Flash session completed the `lookup_savings_balance` goal end-to-end against the live app; see `evidence/README.md`.
- **Deliberately mocked:** the operator console UI is a bare CLI (per the assignment's explicit scope note in Section 3.6) - the pause/cede/resume mechanism underneath it is real.