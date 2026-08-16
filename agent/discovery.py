"""
Discovery run: the LLM drives a real browser via Playwright, using the
accessibility tree as its observation, until the goal is met or a stopping
condition is hit. The successful run is recorded as a Capability artifact,
decoupled from the raw model transcript (the transcript is kept separately,
in evidence/, for debugging - it is not part of the artifact contract).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from agent.llm_client import LLMClient
from agent.perception import observe
from agent.safety import AllowlistPolicy, SafetyGate, GuardrailViolation, classify_risk
from agent.schema import (
    Capability, Step, Locator, LocatorKind, ActionType, RiskLevel, Checkpoint, Outcome,
    InputParam, OutputField,
)
from agent import escalation

CDP_PORT = 9222

MAX_STEPS = 15
STEP_TIMEOUT_S = 10

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = ROOT / "evidence"
ARTIFACTS_DIR = ROOT / "artifacts"


def _log(run_dir: Path, entry: dict):
    entry["ts"] = datetime.now(timezone.utc).isoformat()
    with open(run_dir / "log.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")


def _resolve(page, role: str, name: str):
    """Resolve a (role, name) pair to a live locator, with a text-based
    fallback for cases where role-based matching doesn't hit (e.g. a role
    the model reported, like a generic 'text' node, that doesn't always
    resolve consistently via role-selector matching across engine versions).
    Mirrors the fallback philosophy in replay.py's _resolve_locator, but at
    discovery time we don't have a pre-recorded chain yet - we build one
    defensively from what the model told us."""
    candidates = []
    if role:
        candidates.append(page.get_by_role(role, name=name, exact=False).first)
    if name:
        candidates.append(page.get_by_text(name, exact=False).first)
    for candidate in candidates:
        try:
            candidate.wait_for(state="visible", timeout=3000)
            return candidate
        except Exception:
            continue
    # last resort: return the first candidate anyway so the caller's own
    # timeout/error still fires with a clear message, rather than a bare None.
    return candidates[0] if candidates else page.get_by_text(name or "", exact=False).first


def run_discovery(
    goal: str,
    start_url: str,
    capability_id: str,
    input_params: list[InputParam] | None = None,
    output_fields: list[OutputField] | None = None,
) -> Path:
    """Runs a real LLM-driven discovery session. Returns the path to the
    saved artifact JSON. Raises if the goal isn't reached within limits."""

    run_id = f"discovery-{uuid.uuid4().hex[:8]}"
    run_dir = EVIDENCE_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    policy = AllowlistPolicy()
    gate = SafetyGate(policy, allow_risky=True)  # discovery operator has explicitly scoped this run
    llm = LLMClient()

    recorded_steps: list[Step] = []
    outputs_captured: dict[str, str] = {}
    outcome_reached: str | None = None
    history: list[dict] = []

    with sync_playwright() as p:
        # remote-debugging port open from the start: this is the seam that lets a
        # human operator attach to this exact live session later if escalation fires.
        headless = os.environ.get("CUA_HEADLESS", "1") != "0"
        browser = p.chromium.launch(headless=headless, args=[f"--remote-debugging-port={CDP_PORT}"])
        page = browser.new_page()

        gate.check("navigate", start_url)
        page.goto(start_url, timeout=STEP_TIMEOUT_S * 1000)
        recorded_steps.append(_navigate_step(len(recorded_steps), start_url))
        _log(run_dir, {"event": "navigate", "url": start_url})

        for i in range(MAX_STEPS):
            obs = observe(page)
            obs_text = obs.to_prompt_text()
            page.screenshot(path=str(run_dir / f"step_{i:02d}.png"))
            _log(run_dir, {"event": "observation", "step": i, "url": obs.url, "n_nodes": len(obs.nodes)})

            decision = llm.decide(goal, obs_text, history)
            history.append({"role": "assistant", "text": f"Chose action: {decision.get('action')} (role={decision.get('role')!r}, name={decision.get('name')!r}, value={decision.get('value')!r})"})
            history.append({"role": "user", "text": "ok, continue"})
            # trim history to keep the transcript bounded
            history = history[-8:]

            _log(run_dir, {"event": "decision", "step": i, "decision": decision})

            action = decision.get("action")

            if action == "done":
                outcome_reached = decision.get("outcome") or "success"
                _log(run_dir, {"event": "done", "outcome": outcome_reached})
                break

            if action == "stuck":
                reason = decision.get("reason") or "unspecified"
                _log(run_dir, {"event": "stuck", "reason": reason})
                page.screenshot(path=str(run_dir / f"stuck_step_{i:02d}.png"))
                cdp_endpoint = f"http://localhost:{CDP_PORT}"
                escalation.request_intervention(
                    run_dir,
                    context={
                        "capability_id": capability_id, "goal": goal, "step_index": i,
                        "current_url": page.url, "reason": reason,
                        "screenshot": f"stuck_step_{i:02d}.png",
                    },
                    cdp_endpoint=cdp_endpoint,
                )
                _log(run_dir, {"event": "escalated", "cdp_endpoint": cdp_endpoint})
                print(f"[discovery] STUCK - escalated. Run: python operator_console.py {run_dir}")
                resume_data = escalation.wait_for_resume(run_dir, timeout=None)
                _log(run_dir, {"event": "resumed", "operator_actions": resume_data.get("actions_taken")})
                # re-observe the live page (now possibly changed by the operator) and continue the loop
                continue

            gate.check(action if action in ("navigate", "click", "fill", "select", "extract") else "click")

            if action == "navigate":
                url = decision.get("url") or ""
                gate.check("navigate", url)
                page.goto(url, timeout=STEP_TIMEOUT_S * 1000)
                recorded_steps.append(_navigate_step(len(recorded_steps), url))
                continue

            # Gemini may omit optional function-call args entirely rather than send an
            # empty string, so decision.get(...) can legitimately return None here -
            # normalize to "" since role/name flow into str-typed Locator fields below.
            role, name = decision.get("role") or "", decision.get("name") or ""
            risk = RiskLevel(classify_risk(page.url))

            if action == "click":
                loc = _resolve(page, role, name)
                loc.click(timeout=STEP_TIMEOUT_S * 1000)
                step = Step(
                    step_id=f"s{len(recorded_steps)}", action=ActionType.CLICK, risk=risk,
                    locator=[Locator(kind=LocatorKind.ROLE_NAME, value=name, role=role), Locator(kind=LocatorKind.TEXT, value=name)],
                    checkpoint=Checkpoint(description=f"url changes or content updates after clicking {name!r}"),
                )
            elif action == "fill":
                value = decision.get("value") or ""
                loc = _resolve(page, role, name)
                loc.fill(value, timeout=STEP_TIMEOUT_S * 1000)
                step = Step(
                    step_id=f"s{len(recorded_steps)}", action=ActionType.FILL, risk=risk,
                    locator=[Locator(kind=LocatorKind.ROLE_NAME, value=name, role=role), Locator(kind=LocatorKind.TEXT, value=name)],
                    value_ref=_infer_param_ref(name, input_params), literal_value=None if _infer_param_ref(name, input_params) else value,
                )
            elif action == "select":
                value = decision.get("value") or ""
                loc = _resolve(page, role, name)
                loc.select_option(value, timeout=STEP_TIMEOUT_S * 1000)
                step = Step(
                    step_id=f"s{len(recorded_steps)}", action=ActionType.SELECT, risk=risk,
                    locator=[Locator(kind=LocatorKind.ROLE_NAME, value=name, role=role), Locator(kind=LocatorKind.TEXT, value=name)],
                    literal_value=value,
                )
            elif action == "extract":
                loc = _resolve(page, role, name)
                text = loc.text_content(timeout=STEP_TIMEOUT_S * 1000) or ""
                out_key = decision.get("value") or "extracted_value"
                outputs_captured[out_key] = text.strip()
                step = Step(
                    step_id=f"s{len(recorded_steps)}", action=ActionType.EXTRACT, risk=RiskLevel.SAFE,
                    locator=[Locator(kind=LocatorKind.ROLE_NAME, value=name, role=role), Locator(kind=LocatorKind.TEXT, value=name)],
                    extract_as=out_key,
                )
            else:
                _log(run_dir, {"event": "unknown_action", "action": action})
                continue

            recorded_steps.append(step)
            _log(run_dir, {"event": "action_executed", "action": action, "role": role, "name": name})

        page.screenshot(path=str(run_dir / "final.png"))
        browser.close()

    if outcome_reached is None:
        raise RuntimeError("Discovery run exhausted MAX_STEPS without reaching a 'done' state.")

    capability = Capability(
        capability_id=capability_id,
        description=goal,
        target={"allowed_domains": policy.allowed_domains, "app": "coreserv-legacy"},
        input_params=input_params or [],
        outputs=output_fields or [],
        steps=recorded_steps,
        outcomes=[
            Outcome(
                name=outcome_reached, kind="success" if outcome_reached == "success" else "business_outcome",
                description=f"Reached during discovery run {run_id}",
                detection=Checkpoint(description="see final observation", expect="visible"),
            )
        ],
        provenance={"discovery_run_id": run_id, "model": "see llm_client.MODEL", "captured_outputs_sample": outputs_captured},
    )

    ARTIFACTS_DIR.mkdir(exist_ok=True)
    artifact_path = ARTIFACTS_DIR / f"{capability_id}.json"
    artifact_path.write_text(capability.model_dump_json(indent=2))
    (run_dir / "artifact.json").write_text(capability.model_dump_json(indent=2))
    _log(run_dir, {"event": "artifact_saved", "path": str(artifact_path)})

    return artifact_path


def _navigate_step(idx: int, url: str) -> Step:
    return Step(step_id=f"s{idx}", action=ActionType.NAVIGATE, risk=RiskLevel.SAFE, literal_value=url)


def _infer_param_ref(field_name: str, input_params: list[InputParam] | None) -> str | None:
    if not input_params:
        return None
    fn = (field_name or "").lower()
    for p in input_params:
        if p.name.lower() in fn or fn in p.name.lower():
            return p.name
    return None
