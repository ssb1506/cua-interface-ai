# Computer-Use Automation System

This is a vertical slice of a computer-use automation pipeline: an LLM does discovery against a live UI, saves what it learned as a reusable "capability" artifact, and that artifact can then be replayed deterministically (typed params, typed outputs, proper error handling) without touching the LLM again. If something goes wrong mid-replay, a human can take over the same live browser session rather than starting fresh.

Design rationale and trade-offs are in `/REPORT.md`. `/evidence/README.md` has the honest breakdown of what's actually been run against the live app vs. what's still pending.

## Setup

Everything in this project runs against a mock target app (`app/server.py`) — a local Flask app, no external calls, nothing hidden. Except for the discovery step, none of this needs internet or API access.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Discovery needs a model. Default is Gemini:
```bash
# get a key at https://aistudio.google.com -> "Get API key" -> "Create API key"
export GEMINI_API_KEY=...
# optional: export CUA_MODEL=gemini-2.5-flash
```
Or Anthropic, if you'd rather use Claude credits:
```bash
export CUA_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-...
```
Replay, the safety tests, and the escalation demo don't call an LLM at all, so no key needed there.

## Target application

`app/server.py` is a small mock legacy bank back-office app — nested tables, no test IDs, no client framework, basically what you'd actually find in the wild. Member search, member detail with balance, an "open sub-account" form, a confirmation screen. It also has a couple of failure modes you can toggle on (`?simulate_perm_denied=1`, `?simulate_session_expired=1`) plus some that come up naturally just from bad input (unknown member ID, invalid form fields). Those are what exercise the error taxonomy described in Section 3.3 of the brief.

Start it in its own terminal before anything else:
```bash
python app/server.py
# serves http://localhost:5055
```

## Demo path

### 1. Deterministic replay (works right now, no key needed)

`open_subaccount.json` was built from a verified manual trace against the live app — `scripts/build_reference_artifacts.py` does the building. `lookup_savings_balance.json` started the same way but has since been overwritten by a real Gemini discovery run (see Step 2 below and `evidence/README.md`).

```bash
# success
python run_replay.py artifacts/lookup_savings_balance.json member_id=12345

# a business outcome, not a crash - member just doesn't exist
python run_replay.py artifacts/lookup_savings_balance.json member_id=99999

# risky action, blocked without confirmation
python run_replay.py artifacts/open_subaccount.json member_id=67890 account_type=CHECKING initial_deposit=50

# same, explicitly confirmed
python run_replay.py artifacts/open_subaccount.json member_id=67890 account_type=CHECKING initial_deposit=50 --allow-risky
```

### 2. LLM-driven discovery

Needs a `GEMINI_API_KEY` (or `ANTHROPIC_API_KEY` with `CUA_PROVIDER=anthropic`).

```bash
python run_discovery.py lookup_savings_balance
# or
python run_discovery.py open_subaccount
```
This writes a fresh `artifacts/<capability_id>.json` plus a full evidence trail — per-step observation, model decision, screenshot — under `evidence/discovery-<run_id>/`. Replay whatever it just produced the same way as step 1:

```bash
python run_replay.py artifacts/lookup_savings_balance.json member_id=12345
```
That's the full loop: goal in, discovery run against the live app, artifact saved, then a deterministic replay of that exact artifact with no LLM involved.

### 3. Human escalation / handoff

No key needed here either.

```bash
python scripts/test_escalation.py
# it pauses and prints an evidence run dir; open a second terminal:
python operator_console.py evidence/<printed-run-dir>
#   operator> fill textbox "" 12345
#   operator> click button Search
#   operator> resume
```
Same live session, same CDP-attached browser and page — this is control transfer, not spinning up a new browser and pretending it's a handoff. `evidence/README.md` has the before/after state that backs that up.

### 4. Tests

```bash
python app/server.py &      # tests replay against the live app
PYTHONPATH=. python -m pytest tests/ -v
```
14 tests covering safety guardrails, artifact schema validation, and live replay (success, business-outcome, and guardrail-block cases, all run for real against `app/server.py`).

## Repository layout

```
app/server.py              mock legacy target application
agent/schema.py            the Capability artifact contract
agent/perception.py        accessibility-tree observation layer
agent/llm_client.py        Gemini client (default) with an Anthropic fallback; forced tool/function-call action schema
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

## What's actually real here

The target app, the artifact schema, deterministic replay (full error taxonomy, run against a live app, not stubbed), the safety guardrails, and the escalation mechanism are all real — the pause/cede/resume handoff is a verified two-process transfer over CDP, not a mock. Discovery itself is also real: a Gemini 2.5 Flash session completed `lookup_savings_balance` end-to-end against the live app (see `evidence/README.md` for the trace).

The one deliberately mocked piece is the operator console UI — it's a bare CLI, per the scope note in Section 3.6 of the assignment. The mechanism underneath it isn't mocked, just the interface.
