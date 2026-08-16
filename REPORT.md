# REPORT

## 1. Architecture

Single process, synchronous, no queues or services. Three seams:

- **Perception** (`agent/perception.py`): turns a live Playwright page into a compact accessibility-tree observation (role + accessible name), not raw HTML and not pixels-only. This is deliberate: it's what makes the same interface plausible against a legacy no-test-ID web app *and* (per Section 3.7) a desktop app later, since OS accessibility APIs expose the same role/name/value shape. Raw HTML was rejected because deeply nested legacy tables balloon token count without adding signal; screenshot-only coordinate control was rejected as the default because it's the most brittle option against server-rendered legacy pages where the DOM/AX tree is actually more stable than the visual layout would suggest.
- **Discovery** (`agent/discovery.py` + `agent/llm_client.py`): an LLM is forced (via tool-use with `tool_choice`) to emit one structured action per turn - never free text. This means the artifact is built directly from typed decisions, not parsed out of a transcript after the fact, which is what "record the successful run as a structured artifact... decoupled from the raw model transcript" actually requires.
- **Replay** (`agent/replay.py`): walks the saved `Capability.steps` with no model involved, using a locator fallback chain per step, and returns a structured `ReplayResult`.

Trade-off: single-process/synchronous is not how this would run in production (an AI agent would call a service, and discovery would likely be an async job) - but the brief explicitly says not to build scaling infrastructure prematurely, so I kept the process boundary as the one seam it's cheap to draw later (discovery and replay are already separate functions with no shared mutable state, so wrapping either in a service is additive, not a rewrite).

## 2. Artifact schema

`agent/schema.py`, `Capability`. Key decisions:

- **Locators are a ranked fallback chain (`list[Locator]`), never a single selector**, each tagged with a `kind` (`role_name` preferred, then `label_text`/`text`, then `css` flagged `brittle=True`). This is the direct answer to "no test IDs, no clean DOM" - a locator strategy that only works for one specific markup snapshot is not durable against enterprise apps that are stable-but-not-static.
- **`outcomes` is a first-class list, separate from `steps`**, each tagged `kind: success | business_outcome`. This is what keeps "no such member" from ever being conflated with a crash in the replay contract - it's a named, documented possibility of the capability, not an afterthought caught in a try/except.
- **Every step carries its own `risk` level and (optionally) its own `checkpoint`** rather than one success condition at the end. This means replay fails at the exact step with expected-vs-observed detail, instead of running to completion and then discovering the goal wasn't actually reached.
- **`input_params`/`outputs` are typed and named independently of the step sequence** - a calling agent needs a function-call-like contract (name in, name out) and should never need to understand `steps` at all; `steps` exists for the replay engine and for human review, not for the caller.
- **`approval_state: draft | approved`** and **`provenance`** (discovery run id, model, captured-output sample) make the artifact reviewable: a human can tell what produced it and whether it's cleared for unattended use, without reading the raw transcript.

## 3. Determinism & error handling

Determinism comes from three things together: (1) the ranked locator fallback chain resolved by role+accessible-name rather than brittle CSS, (2) an explicit `wait_for(state="visible", timeout=...)` on every resolution instead of fixed sleeps, and (3) a per-step checkpoint verified before moving on.

Error/outcome taxonomy, exactly as specced in 3.3:

- **Expected business outcomes**: detected via known page markers (e.g. "No record found", "Validation error(s)") checked immediately after every step. Returned as `status="business_outcome"` with a named `business_outcome` field - a legitimate answer, never an exception.
- **Recoverable conditions**: the locator fallback chain itself is the recovery mechanism for the common case (primary locator times out → try the next). I did not implement automatic dismissal of unexpected interstitials beyond that, for lack of an interstitial in the target app to test against honestly - flagged in Cuts.
- **Hard failures**: `locator_not_found` (no candidate in the fallback chain resolved - includes which locators were tried and the URL) and `checkpoint_failed` (the step executed but its assertion didn't hold) both return `status="failure"` with `{step, expected, observed}`. `session_expired` is treated as a hard failure rather than a guessed recovery, since re-authenticating is a decision this artifact has no authority to make - it's flagged for escalation instead.

All five paths (success, not-found, validation-error, guardrail-block, and a risky-action success) are exercised for real against the live app in `/evidence/`.

## 4. Heterogeneity & multi-tenant

**Surface abstraction.** The seam is exactly the perception/replay boundary already in the code: `perception.py` (discovery) and `_resolve_locator` (replay) are the only places that know *how* to read/act on a concrete surface. Extending to a legacy web app needs no schema change at all - the same role/name locators apply, which is why the target app in this project already is one. Extending to desktop means swapping the perception+resolution implementation for one built on OS accessibility APIs (Windows UI Automation / macOS Accessibility) instead of Playwright's accessibility snapshot - `Locator.kind` would gain an `ax_path` variant, but `Capability`, `Step`, and `ReplayResult` are unchanged, because they were deliberately built surface-agnostic (a `Locator` is "role + name + fallback kind," never "a Playwright selector").

**Multi-tenant reuse.** None of this is built (per the brief's instruction not to), but the design: (1) `Capability.target` already carries a scope dict (`allowed_domains`, `app`) - this generalizes to a `(vendor_app, version)` key so one canonical artifact is recorded per vendor product, not per tenant; (2) a tenant-specific override layer sits on top - a small diff (branding strings, a renamed field label, a different route prefix) applied to the canonical artifact's locators/URLs at replay time, versioned separately so a branding change in one tenant doesn't force re-recording for the other 199; (3) drift detection: since replay already reports `locator_not_found`/`checkpoint_failed` with full expected-vs-observed detail, a fleet of scheduled dry-run replays per tenant is the natural drift signal - a spike in a specific step's failure rate for one tenant, with others stable, flags that tenant's override as stale without touching the canonical artifact.

## 5. Escalation & handoff

Control-transfer model: the browser is launched with a CDP remote-debugging port open from the start (`--remote-debugging-port`). "Control" is just "who currently holds a Playwright/CDP connection and is issuing commands" - automation and the human operator are two clients of the *same* browser process, never two sessions. On a stuck/blocked state, automation writes `intervention_request.json` (goal, capability, current step/URL, reason, screenshot, the CDP endpoint) and blocks on a poll for `resume_signal.json`. The mocked operator console (`operator_console.py`, deliberately bare per Section 3.6's scope note) connects to that same CDP endpoint, drives the same live page, and on `resume` writes back a summary of what it did; automation's wait unblocks and re-observes the *same* page object with no new session created.

This was verified end-to-end as a real two-process run (not simulated in the same call stack): `evidence/escalation_stuck_and_resume/` shows automation pausing on the home page and, after the operator manually filled and submitted the search form, resuming to find the live page's title had changed to the member-detail page - proof the state carried across the handoff on one session.

Currently wired for the "stuck" (discovery can't decide) and "risky action needs confirmation" (replay) cases. Not yet wired: automatically resuming a *replay* run (not just discovery) from mid-flow after a hard failure - right now a replay failure just returns to the caller; routing it through the same escalation path instead of just failing is the natural next step (noted in Cuts).

## 6. Safety

`agent/safety.py`. An `AllowlistPolicy` (domains + route prefixes + allowed action types) is checked before every navigate/action, both during discovery and replay - not just at recording time, so a replayed artifact can't be pointed at a URL outside its recorded scope even if the params are manipulated. Every step carries a `risk: safe | risky` classification; `risky` (currently: anything that creates/mutates state, e.g. `/subaccount/create`) is blocked by default and only proceeds with an explicit `allow_risky=True` from the caller - conservative-by-default, per the brief's instruction to handle the risky class conservatively rather than silently confirm. Redaction (`redact`/`redact_dict`) strips any field whose *name* matches a sensitive-data pattern (password, SSN, card number, API key, token) before it can reach a log or artifact - applied structurally, not as an afterthought grep over already-written files.

Limits: the risk classification is a static per-route allowlist (`RISKY_ROUTES`), not learned or content-inspected - a new state-mutating route added to the target app without updating that set would be silently treated as safe. Redaction is pattern-on-field-name, not content-based, so a sensitive value entered into an oddly-named field wouldn't be caught. Both are reasonable defaults for a small project but not a substitute for a real DLP layer in production.

## 7. Cuts

What's cut and why:

- **The LLM discovery run was executed for real** (see `evidence/README.md`) - a genuine Gemini-2.5-Flash session completed the `lookup_savings_balance` goal in 4 steps with no escalation needed, converging on almost the same flow as the hand-authored reference artifact. Getting this working end-to-end surfaced several small provider-integration gaps not visible from design alone: Gemini's function-calling schema needs uppercase type names unlike standard JSON Schema; conversation history has to be built in Gemini's own `Content`/`Part` shape rather than reused from an Anthropic-style transcript; Gemini can omit an optional function argument entirely rather than send an empty string, which needed defensive `None`-coalescing everywhere a decision field feeds a typed `Locator`; the free tier's 5-requests/minute cap needed a retry-with-backoff wrapper; and `get_by_role("text", ...)` matching on a generic text node wasn't fully reliable, which needed a `get_by_text` fallback. None of this touches the architecture - it's contained in `agent/llm_client.py`'s provider adapter and one resolution helper in `agent/discovery.py` - but it's a concrete illustration of why the brief insists the discovery run has to be real: several of these would not have surfaced from design or a mocked LLM alone.
- **Recoverable-condition auto-handling beyond the locator fallback chain** (e.g., auto-dismissing an unexpected confirm dialog) - not implemented because the target app doesn't produce one, and I didn't want to fabricate a fake interstitial just to exercise a code path with no real analog.
- **Replay-time escalation** - a replay hard failure currently just returns to the caller rather than routing through the same pause/resume mechanism discovery uses. The mechanism is generic enough to reuse; wiring it in is mechanical, not a design gap, but it wasn't done.
- **Multi-tenant/desktop support** - by design, per the brief (Section 3.7 explicitly says design, not build).
- **Assisted fallback, confidence scoring, code generation, multi-run stability** (Section 8 stretch goals) - skipped entirely; depth on the core six requirements was prioritized over any stretch goal, per Section 5's explicit guidance.

What I'd build next with more time: run discovery against the `open_subaccount` goal too (only `lookup_savings_balance` was run for real) and compare; wire replay failures into escalation; and replace the static `RISKY_ROUTES` allowlist with a content-based check (e.g., detecting `method="POST"`/state-mutating forms) so risk classification doesn't silently rot as the target app changes.
