# Evidence

## Status: all evidence is real and executed - replay, escalation, and discovery.

### Real, executed replay evidence (no LLM - deterministic replay engine against the live app)

| Folder | Scenario | Result |
|---|---|---|
| `replay_success_lookup_balance/` | valid member ID | `success`, balance extracted |
| `replay_business_outcome_member_not_found/` | unknown member ID | `business_outcome: not_found` |
| `replay_guardrail_block_risky_unconfirmed/` | risky step (create sub-account) without `allow_risky` | `failure: guardrail_block` |
| `replay_success_open_subaccount/` | risky step, confirmed | `success`, confirmation text extracted |
| `replay_business_outcome_validation_error/` | missing required form field | `business_outcome: validation_error` |

Each folder has `params.json` (inputs used), `result.json` (the structured `ReplayResult`), `log.jsonl` (structured step-by-step log), and a screenshot at the terminal state. Regenerate with:
```
python app/server.py &
python scripts/generate_replay_evidence.py
```

### Real, executed escalation evidence

`escalation_stuck_and_resume/` - a live automation session pauses (`intervention_request.json`, with the CDP endpoint and reason), a separate operator process (`operator_console.py`) attaches to that *same* live browser session over CDP, performs manual steps, and writes `resume_signal.json`. Automation's `wait_for_resume()` unblocks and confirms it is looking at the post-handoff state of the *same* page object (page title changes from the pre-handoff state to `Member 67890` after resume, with no new browser/page created). `stuck.png` / `after_resume.png` show the before/after state.

Reproduce with two terminals:
```
python app/server.py &
python scripts/test_escalation.py
# in a second terminal, once it prints the run dir:
python operator_console.py evidence/escalation_stuck_and_resume
#   operator> fill textbox "" 67890
#   operator> click button Search
#   operator> resume
```

### Discovery run evidence - real, executed

`evidence/discovery-<run_id>/` - a genuine Gemini-2.5-Flash-driven session against the live target app, goal: "Look up member 12345 and read their current savings balance."

The model completed the goal in 4 steps with no escalation needed:
1. `fill` the member ID textbox with `12345`
2. `click` the Search button
3. `extract` the savings balance text node
4. `done` (outcome: `success`)

Notably, this converged on almost exactly the same flow as the hand-authored reference artifact in `/artifacts/` (same step count, same shape: navigate -> fill -> click -> extract) - a good sign the artifact schema is expressive enough for an LLM to arrive at a sound capability unaided, not just something a human had to hand-tune around.

The resulting `artifact.json` in this folder (and the overwritten `artifacts/lookup_savings_balance.json`) was verified with real replay runs, no LLM involved:
```
python run_replay.py artifacts/lookup_savings_balance.json member_id=12345
# -> {"status": "success", "outputs": {"savings_balance": "$4,213.55"}, ...}

python run_replay.py artifacts/lookup_savings_balance.json member_id=99999
# -> {"status": "business_outcome", "business_outcome": "not_found", ...}
```

`log.jsonl` in this folder has the full observation/decision trace; per-step screenshots (`step_00.png` etc.) show what the model actually saw at each turn.

Reproduce with:
```bash
export GEMINI_API_KEY=...   # free tier, no payment method required - https://aistudio.google.com
python app/server.py &
python run_discovery.py lookup_savings_balance
```
