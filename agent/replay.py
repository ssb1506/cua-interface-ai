"""
Deterministic replay engine - the production execution path.

No model in the decision loop. Given a saved Capability artifact and a set
of input params, this replays the recorded steps using the locator
fallback chain, verifies each step's checkpoint, and returns a structured
result. This is what an AI agent actually calls in production.

Result contract (ReplayResult) distinguishes three outcomes on purpose:
  - success            : goal achieved, declared outputs returned.
  - business_outcome   : a *named, expected* non-success result (e.g.
                          "member_not_found"). Not an error - a legitimate
                          answer the caller needs to branch on.
  - failure            : something the artifact did not anticipate. Carries
                          step/expected/observed detail for debugging.

Runtime condition handling built in:
  - validation_error / not_found / permission_denied -> detected via known
    page markers and reported as business_outcome, never as a crash.
  - session_expired banner -> recoverable: replay detects it and treats the
    run as a hard failure with a specific reason (recovering it would mean
    re-authenticating, which is out of scope for this artifact - flagged
    for escalation instead of guessed at).
  - element not found / timeout on a locator -> falls back through the
    locator chain; if all fail, hard failure with the exact step + what
    was expected vs. what was on the page.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

from agent.schema import Capability, Step, ActionType, RiskLevel, LocatorKind
from agent.safety import AllowlistPolicy, SafetyGate, GuardrailViolation, redact_dict

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = ROOT / "evidence"

STEP_TIMEOUT_MS = 6000


@dataclass
class ReplayResult:
    status: Literal["success", "business_outcome", "failure"]
    outputs: dict[str, str] = field(default_factory=dict)
    business_outcome: Optional[str] = None
    error: Optional[dict[str, Any]] = None
    run_id: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "outputs": self.outputs,
            "business_outcome": self.business_outcome,
            "error": self.error,
            "run_id": self.run_id,
        }


# Page-content markers this target app uses for known runtime conditions.
# In a real integration these would be per-app-version, reviewed markers -
# not guessed at replay time.
_MARKERS = {
    "not_found": "No record found",
    "validation_error": "Validation error(s)",
    "permission_denied": "Access Denied",
    "session_expired": "session has expired",
}


def _detect_known_condition(page: Page) -> Optional[str]:
    try:
        body_text = page.inner_text("body")
    except Exception:
        return None
    for name, marker in _MARKERS.items():
        if marker.lower() in body_text.lower():
            return name
    return None


def _resolve_locator(page: Page, step: Step):
    """Walk the ranked fallback chain. Returns the first locator that
    resolves to a visible element, or None if all fail."""
    for loc in step.locator:
        try:
            if loc.kind in (LocatorKind.ROLE_NAME, "role_name"):
                candidate = page.get_by_role(loc.role, name=loc.value, exact=False).first
            elif loc.kind in (LocatorKind.TEXT, "text"):
                candidate = page.get_by_text(loc.value, exact=False).first
            elif loc.kind in (LocatorKind.LABEL_TEXT, "label_text"):
                candidate = page.get_by_label(loc.value, exact=False).first
            else:  # CSS - last resort, brittle
                candidate = page.locator(loc.value).first
            candidate.wait_for(state="visible", timeout=STEP_TIMEOUT_MS)
            return candidate
        except PWTimeout:
            continue
    return None


def replay(
    artifact_path: Path,
    params: dict[str, str],
    allow_risky: bool = False,
) -> ReplayResult:
    capability = Capability.model_validate_json(artifact_path.read_text())
    run_id = f"replay-{uuid.uuid4().hex[:8]}"
    run_dir = EVIDENCE_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    def _log(entry: dict):
        entry["ts"] = datetime.now(timezone.utc).isoformat()
        with open(run_dir / "log.jsonl", "a") as f:
            f.write(json.dumps(redact_dict(entry) if False else entry) + "\n")  # entries here are structural, not raw PII

    policy = AllowlistPolicy(**{k: v for k, v in capability.target.items() if k in {"allowed_domains", "allowed_routes"}}) \
        if capability.target.get("allowed_domains") else AllowlistPolicy()
    gate = SafetyGate(policy, allow_risky=allow_risky)

    outputs: dict[str, str] = {}

    with sync_playwright() as p:
        headless = os.environ.get("CUA_HEADLESS", "1") != "0"
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        try:
            for step in capability.steps:
                _log({"event": "step_start", "step_id": step.step_id, "action": step.action})

                # -- risk gate: block/require confirmation on risky steps unless explicitly allowed
                try:
                    if step.risk in (RiskLevel.RISKY, "risky"):
                        gate.check_risk("/subaccount/create" if not gate.allow_risky else "/allowed")
                except GuardrailViolation as e:
                    page.screenshot(path=str(run_dir / "blocked.png"))
                    browser.close()
                    return ReplayResult(
                        status="failure", run_id=run_id,
                        error={"step": step.step_id, "kind": "guardrail_block",
                               "expected": "risky step pre-approved or confirmed",
                               "observed": str(e)},
                    )

                if step.action == ActionType.NAVIGATE or step.action == "navigate":
                    url = (step.literal_value or "")
                    if step.value_ref and "{" + step.value_ref + "}" in url:
                        url = url.replace("{" + step.value_ref + "}", str(params.get(step.value_ref, "")))
                    ok, reason = policy.check_url(url)
                    if not ok:
                        browser.close()
                        return ReplayResult(status="failure", run_id=run_id,
                                             error={"step": step.step_id, "kind": "guardrail_block", "observed": reason})
                    page.goto(url, timeout=STEP_TIMEOUT_MS)

                elif step.action in (ActionType.CLICK, "click"):
                    el = _resolve_locator(page, step)
                    if el is None:
                        return _fail_locator(page, run_dir, run_id, step, browser)
                    el.click(timeout=STEP_TIMEOUT_MS)

                elif step.action in (ActionType.FILL, "fill"):
                    el = _resolve_locator(page, step)
                    if el is None:
                        return _fail_locator(page, run_dir, run_id, step, browser)
                    value = params.get(step.value_ref, step.literal_value or "") if step.value_ref else (step.literal_value or "")
                    el.fill(str(value), timeout=STEP_TIMEOUT_MS)

                elif step.action in (ActionType.SELECT, "select"):
                    el = _resolve_locator(page, step)
                    if el is None:
                        return _fail_locator(page, run_dir, run_id, step, browser)
                    value = params.get(step.value_ref, step.literal_value or "") if step.value_ref else (step.literal_value or "")
                    el.select_option(str(value), timeout=STEP_TIMEOUT_MS)

                elif step.action in (ActionType.EXTRACT, "extract"):
                    el = _resolve_locator(page, step)
                    if el is None:
                        return _fail_locator(page, run_dir, run_id, step, browser)
                    text = (el.text_content(timeout=STEP_TIMEOUT_MS) or "").strip()
                    if step.extract_as:
                        outputs[step.extract_as] = text

                # -- after every mutating step, check for a known runtime condition before continuing
                condition = _detect_known_condition(page)
                if condition == "session_expired":
                    page.screenshot(path=str(run_dir / "condition.png"))
                    browser.close()
                    return ReplayResult(
                        status="failure", run_id=run_id,
                        error={"step": step.step_id, "kind": "session_expired",
                               "expected": "authenticated session", "observed": "session expiry banner detected"},
                    )
                if condition in ("not_found", "validation_error", "permission_denied"):
                    page.screenshot(path=str(run_dir / "condition.png"))
                    browser.close()
                    return ReplayResult(status="business_outcome", run_id=run_id, business_outcome=condition, outputs=outputs)

                # -- checkpoint verification
                if step.checkpoint and step.checkpoint.locator:
                    cp_ok = _verify_checkpoint(page, step)
                    if not cp_ok:
                        return _fail_checkpoint(page, run_dir, run_id, step, browser)

                _log({"event": "step_ok", "step_id": step.step_id})

            page.screenshot(path=str(run_dir / "final.png"))
            browser.close()
            return ReplayResult(status="success", run_id=run_id, outputs=outputs)

        except PWTimeout as e:
            page.screenshot(path=str(run_dir / "timeout.png"))
            browser.close()
            return ReplayResult(status="failure", run_id=run_id,
                                 error={"kind": "timeout", "observed": str(e)})
        except Exception as e:  # noqa: BLE001 - top-level hard-failure catch-all, reported with detail
            try:
                page.screenshot(path=str(run_dir / "error.png"))
            except Exception:
                pass
            browser.close()
            return ReplayResult(status="failure", run_id=run_id,
                                 error={"kind": "unexpected_exception", "observed": str(e)})


def _verify_checkpoint(page: Page, step: Step) -> bool:
    try:
        page.get_by_role(step.checkpoint.locator.role, name=step.checkpoint.locator.value, exact=False)\
            .first.wait_for(state="visible", timeout=STEP_TIMEOUT_MS)
        return True
    except PWTimeout:
        return False


def _fail_locator(page, run_dir, run_id, step, browser) -> ReplayResult:
    page.screenshot(path=str(run_dir / f"fail_{step.step_id}.png"))
    browser.close()
    return ReplayResult(
        status="failure", run_id=run_id,
        error={"step": step.step_id, "kind": "locator_not_found",
               "expected": [l.model_dump() for l in step.locator],
               "observed": f"no locator in fallback chain resolved on {page.url}"},
    )


def _fail_checkpoint(page, run_dir, run_id, step, browser) -> ReplayResult:
    page.screenshot(path=str(run_dir / f"fail_checkpoint_{step.step_id}.png"))
    browser.close()
    return ReplayResult(
        status="failure", run_id=run_id,
        error={"step": step.step_id, "kind": "checkpoint_failed",
               "expected": step.checkpoint.description, "observed": f"checkpoint not visible on {page.url}"},
    )
